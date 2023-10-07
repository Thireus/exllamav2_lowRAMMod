
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exllamav2 import (
    ExLlamaV2,
    ExLlamaV2Config,
    ExLlamaV2Cache,
    ExLlamaV2Tokenizer,
    ExLlamaV2Lora,
)

from exllamav2.generator import (
    ExLlamaV2StreamingGenerator,
    ExLlamaV2Sampler
)

import time

# Initialize model and cache

model_directory = "/mnt/str/models/_gptq/TheBloke_Llama-7B-GPTQ/"
config = ExLlamaV2Config()
config.model_dir = model_directory
config.prepare()

model = ExLlamaV2(config)
print("Loading model: " + model_directory)
model.load()

tokenizer = ExLlamaV2Tokenizer(config)

cache = ExLlamaV2Cache(model)

# Load LoRA

lora_directory = "/mnt/str/models/_test_loras/tloen_alpaca-lora-7b/"
lora = ExLlamaV2Lora.from_directory(model, lora_directory)

# Initialize generator

generator = ExLlamaV2StreamingGenerator(model, cache, tokenizer)
generator.warmup()
generator.set_stop_conditions([tokenizer.eos_token_id])

# Sampling settings

settings = ExLlamaV2Sampler.Settings()
settings.temperature = 0.85
settings.top_k = 50
settings.top_p = 0.8
settings.token_repetition_penalty = 1.1

# Alpaca-style prompt

prompt = \
    "Below is an instruction that describes a task. Write a response that appropriately completes the request.\n" \
    "\n" \
    "### Instruction:\n" \
    "Write three tweets explaining that the Earth is not flat, using spaghetti and meatballs as an analogy.\n" \
    "\n" \
    "### Response:"


# Generate with and without LoRA

def generate_with_lora(prompt_, lora_, max_new_tokens):

    input_ids = tokenizer.encode(prompt_)

    print (prompt, end = "")
    sys.stdout.flush()

    generator.begin_stream(input_ids, settings, loras = [lora_])
    generated_tokens = 0
    while True:
        chunk, eos, _ = generator.stream()
        generated_tokens += 1
        print (chunk, end = "")
        sys.stdout.flush()
        if eos or generated_tokens == max_new_tokens: break

    print()


print()
print("--------------------------")
print("No LoRA:")
print()

generate_with_lora(prompt, None, 250)

print()
print("--------------------------")
print("Yes LoRA:")
print()

generate_with_lora(prompt, lora, 250)