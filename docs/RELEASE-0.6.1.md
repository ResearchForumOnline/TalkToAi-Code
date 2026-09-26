# TalkToAi Code 0.6.1

A reliability and research follow-up to 0.6.0.

## Changes

- Completion checks reject unresolved tool failures and still-running owned jobs. Keep going offers one bounded repair review before an explicit unfinished pause.
- Windows control IDs are unique to each inspection; stale IDs cannot silently target a new control. Input checks current visibility, enabled state and password-field status.
- Research journal reads can rehash current evidence files and report changed, missing, blocked or unstable artifacts without rewriting historical records. This verifies file consistency, not scientific validity.
- Model choices now includes read-only local/server diagnostics: distinguish unreachable runtimes, malformed inventory and missing model IDs, with concrete recovery guidance. No model loading, inference, downloads or configuration changes.
- Exported task reports include the saved execution checkpoint and blockers.

## Verification

Final Windows regression and native acceptance results are recorded below before release. Linux/macOS source smoke workflow is run separately. The 0.6.0 real server-model coding trial and speed comparison remain historical evidence; this patch does not claim a new model benchmark.

## Research

- https://www.anthropic.com/research/yes-claude-can-do-nine-loops
- https://ollama.com/blog/streaming-tool
- https://github.com/ollama/ollama/blob/main/docs/context-length.mdx

Relevant desktop research review used public index headings about source ledgers and methods. No private results were imported. Bounded continuation and reproducibility records support iterative work; they do not promise autonomous discoveries, frontier-model parity or changed model weights.
