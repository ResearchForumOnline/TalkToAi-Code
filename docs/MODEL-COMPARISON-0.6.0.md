# Installed coding route comparison — 0.6.0

Measured on 26 September 2026 using the same two public, isolated fixtures and a 4,096-token context. The comparison used native Ollama tool calls, temperature 0, a 96-token response limit and a 240-second budget per route. No project data was sent and generated tool calls were validated without execution.

| Installed route | Code fixture | Native tool fixture | Generation rate | Observed elapsed |
| --- | --- | --- | --- | --- |
| Server: openzero-qwen3-coder-30b-a3b-q3:latest | Passed | Passed | 15.564 tokens/s code; 15.495 tokens/s tool | 31.187s code including 28.143s load; 6.500s tool |
| Local PC: smtek/Qwen3.8-27B:latest | Passed | Incomplete at route time limit | 1.133 tokens/s code | 108.578s code including 61.154s load |

The server model was retained as this user's default. The local model remains selectable. This is a comparison of complete routes on different hardware, with different quantization and model architecture, under the current desktop workload. It is not a hardware-independent ranking, comprehensive quality benchmark, or evidence that the 27B model cannot use tools. First-token/load time differs from steady generation speed.

The two fixtures ask for a four-line Python clamp implementation (AST-restricted evaluation against four cases) and one exact native set_player_name tool call. Neither checks broad agent capability. The separate [live coding acceptance](AMD-ACCEPTANCE-0.6.0.md) records actual source repair using the server model at 8K context, including its slow behavior and limitations.

Reproduce a fresh comparison using Models → Compare installed coding models. The comparison does not download models or automatically change a user's route. Generic installations use the name Server; a personal server label such as AMD is configurable.
