from __future__ import annotations
import torch
from exllamav2.compat import safe_move_tensor

class Params:
    batch_size: int
    seq_len: int
    past_len: int | None
    past_lens: list[int] | None
    input_mask: torch.Tensor | None
    multi_cache: bool
    attn_mask: torch.Tensor | None
    attn_masks: torch.Tensor | None
    position_offsets: torch.Tensor | None
    past_lens_tensor: torch.Tensor | None
    paged: bool

    def __init__(
            self,
            batch_size: int,
            seq_len: int | None = None,
            past_len: int | list[int] | None = None,
            input_mask: torch.Tensor | None = None,
            position_offsets: torch.Tensor | None = None,
            paged=False
    ):

        self.batch_size = batch_size
        self.paged = paged

        if paged: return

        self.seq_len = seq_len
        if isinstance(past_len, list):
            self.past_len = None
            self.past_lens = past_len
            self.multi_cache = True
        else:
            self.past_len = past_len
            self.past_lens = None
            self.multi_cache = False
        self.input_mask = input_mask

        self.attn_mask = None
        self.attn_masks = None

        self.position_offsets = position_offsets
        self.past_lens_tensor = None
        self.paged = paged

    def is_causal(self) -> bool:
        return self.input_mask is None

    def get_position_offsets(self, device) -> torch.Tensor | None:
        assert self.position_offsets is not None
        if self.position_offsets.device != device:
            self.position_offsets = safe_move_tensor(self.position_offsets, device)
        return self.position_offsets

    def get_past_lens(self, device) -> torch.Tensor | None:
        assert self.past_lens is not None
        if self.past_lens_tensor is None:
            self.past_lens_tensor = torch.tensor(self.past_lens, dtype=torch.int, device=device)
        elif self.past_lens_tensor.device != device:
            self.past_lens_tensor = safe_move_tensor(self.past_lens_tensor, device)
        return self.past_lens_tensor

    def get_attn_mask(self, device, force: bool = False) -> torch.Tensor | None:
        if self.attn_mask is None:
            self.attn_mask = self.build_attn_mask(device, force)
        elif self.attn_mask.device != device:
            self.attn_mask = safe_move_tensor(self.attn_mask, device)
        return self.attn_mask

    def get_attn_masks(self, device) -> torch.Tensor | None:
        if self.attn_masks is None:
            self.attn_masks = self.build_attn_masks(device)
        elif self.attn_masks[0] is not None and self.attn_masks[0].device != device:
            self.attn_masks = [(safe_move_tensor(m, device) if m is not None else None) for m in self.attn_masks]
        return self.attn_masks

    def build_single_attn_mask(self, batch_size, seq_len, past_len, device, input_mask):
        attn_mask = torch.zeros((batch_size, 1, seq_len, past_len + seq_len), dtype=torch.float16, device=device)
        attn_mask_triu = torch.triu(torch.full((seq_len - 1, seq_len - 1), -65504.0))
        attn_mask[:, :, : seq_len - 1, past_len + 1: past_len + seq_len] = attn_mask_triu
        if input_mask is not None:
            min_mask_width = min(input_mask.shape[-1], seq_len + past_len)
            input_mask_part = safe_move_tensor(input_mask[:, :min_mask_width], attn_mask.device)
            input_mask_part = input_mask_part.unsqueeze(1).unsqueeze(2)
            attn_mask[:, :, :, :min_mask_width] = torch.minimum(attn_mask[:, :, :, :min_mask_width], input_mask_part)
        return attn_mask

    def build_attn_mask(self, device, force: bool = False) -> torch.Tensor | None:
        assert not self.multi_cache, "Building single mask for multiple caches"
        if self.input_mask is None and self.seq_len == 1 and not force: return None
        return self.build_single_attn_mask(self.batch_size, self.seq_len, self.past_len, device, self.input_mask)

    def build_attn_masks(self, device) -> torch.Tensor | None:
        assert self.multi_cache, "Building multiple masks for single cache"
        attn_masks = []
        for i, past_len in enumerate(self.past_lens):
            if self.input_mask is None and self.seq_len == 1:
                attn_masks.append(None)
            else:
                attn_masks.append(self.build_single_attn_mask(1, self.seq_len, past_len, device, self.input_mask[i]))
        return attn_masks


class PagedParams(Params):

    block_index: torch.Tensor
    cache_seqlens: torch.Tensor
    max_cache_seqlen: int
    page_size: int
    is_sequential: bool
    first_index: int

    def __init__(
        self,
        batch_size: int,
        block_index: torch.Tensor,
        cache_seqlens: torch.Tensor,
        max_cache_seqlen: int,
        page_size: int,
        q_len: int = 0
    ):
        super().__init__(
            batch_size = batch_size,
            paged = True
        )

        self.block_index = block_index
        self.cache_seqlens = cache_seqlens
        self.max_cache_seqlen = max_cache_seqlen
        self.page_size = page_size

        self.is_sequential = False
        assert self.block_index.device.type == "cpu"
        assert self.cache_seqlens.device.type == "cpu"
        assert q_len > 0
        if self.block_index.shape[0] == 1:
            vi0 = self.cache_seqlens[0].item()
            vi1 = vi0 + q_len
            vp0 = vi0 // page_size
            vp1 = (vi1 - 1) // page_size
            for i in range(vp0 + 1, vp1 + 1):
                if self.block_index[0, i].item() != self.block_index[0, i - 1].item() + 1:
                    break
            else:
                self.is_sequential = True
                self.first_index = self.block_index[0, vp0].item() * page_size + vi0 - vp0 * page_size
                self.cache_seqlens_after = self.cache_seqlens + q_len

    def get_attn_mask(self, device, force: bool = False):
        raise NotImplementedError()

    def get_block_index(self, device) -> torch.Tensor:
        if self.block_index.device != device:
            self.block_index = safe_move_tensor(self.block_index, device)
        return self.block_index

    def get_cache_seqlens(self, device_idx: int) -> torch.Tensor:
        if self.cache_seqlens.device.index != device_idx:
            self.cache_seqlens = safe_move_tensor(self.cache_seqlens, device_idx, non_blocking = True)
        return self.cache_seqlens

    def get_cache_seqlens_after(self, device_idx: int) -> torch.Tensor:
        if self.cache_seqlens_after.device.index != device_idx:
            self.cache_seqlens_after = safe_move_tensor(self.cache_seqlens_after, device_idx, non_blocking = True)
        return self.cache_seqlens_after