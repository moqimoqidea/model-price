# GPT-6.1 Sol

- Model ID: gpt-6.1-sol
- Model type: reasoning
- Current snapshot: gpt-6.1-sol

Near-Astra performance for complex work at a lower cost.

GPT-6.1 Sol delivers near-Astra performance at a lower cost for complex coding,
computer use, and professional work. Compare it with Astra on your tasks to
assess the tradeoff between quality and cost.

`reasoning.effort` supports `low`, `medium` (default), `high`, `xhigh`, and
`max`. The `none` and `minimal` reasoning efforts are not supported.

Use the Responses API for tool calling. Chat Completions is supported without
tool calling.

For the fastest response speeds, use [Ultrafast mode](https://developers.openai.com/api/docs/guides/ultrafast-mode)
with `model: "gpt-6.1-sol"` and `service_tier: "ultrafast"` in the Responses API.

GPT-6.1 Sol supports US and EU data residency, including with Fast and Ultrafast
modes. See [data residency eligibility](https://developers.openai.com/api/docs/guides/your-data#which-models-and-features-are-eligible-for-data-residency).

See [GPT-6.1 Sol in the GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra#gpt-61-sol) and
[model-selection guidance](https://developers.openai.com/api/docs/guides/model-selection#when-to-consider-gpt-61-sol).

## Snapshots
- gpt-6.1-sol

## Current snapshot details
- Context window: 1,050,000 tokens
- Max input: 922,000 tokens
- Max output: 128,000 tokens
- Knowledge cutoff: 2026-04-30
- Input modalities: text, image
- Output modalities: text
- Supported endpoints: responses, chat_completions, batch
- Supported features: streaming, structured_outputs, function_calling, file_search, image_input, web_search, prompt_caching

## Supported tools
- web_search
- file_search
- image_generation
- code_interpreter
- hosted_shell
- apply_patch
- skills
- computer_use
- mcp
- tool_search

## Pricing notes
- Cached input tokens are priced at 5% of the uncached input token rate.
- Cache writes are billed at 1.25x the uncached input token rate.
- Prompts with more than 272K input tokens are priced at 2x input and cache rates and 1.5x output for the full request.
- Fast mode prices are 2x Standard. Batch and Flex prices are 50% lower than Standard.
- Ultrafast mode prices are 6x Standard.
- Regional processing adds a 10% premium where available.

Playground: https://platform.openai.com/chat/edit?models=gpt-6.1-sol
