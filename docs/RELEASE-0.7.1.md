# TalkToAi Code 0.7.1

## Qwen tool compatibility and chat repair

The reported NIGHTFALL conversation contained complete function calls missing the opening tool_call wrapper. The app treated those as text and retried with guidance that conflicted with the Qwen3-Coder template.

- Validate complete Qwen function batches against currently enabled tool schemas before executing them, including the observed missing-wrapper form.
- Reject incomplete, truncated, unknown or ambiguous calls without executing any part of the batch.
- Preserve tool permissions; only current assistant responses enter the adapter.
- Suppress streamed protocol fragments and empty assistant tool-call rows.
- Preserve original history while omitting old malformed protocol from model-context copies.
- Clear the visible Tools panel for new runs while preserving saved activity and genuine errors.
- Correct garbled progress separators.

## Verification

282 tests passed on Windows with native acceptance enabled. Regression cases include the exact project_info/list_files response reported by the user, chunk-split tags, rejected malformed batches and UI history rendering.

The real AMD 30B model returned a valid native project_info call in 20.5 seconds. Executing that read-only call identified NIGHTFALL as Godot; the project manifest hash stayed unchanged. That live response did not require text recovery; the captured malformed responses are covered by regression tests.

Packaged Windows self-test and GUI startup passed. Linux/macOS installation, offscreen startup and portable regressions passed: https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36279434165

## References

- https://github.com/ollama/ollama/issues/18530
- https://github.com/ollama/ollama/issues/16686
- https://github.com/ollama/ollama/blob/main/model/parsers/qwen3coder.go
- https://docs.ollama.com/capabilities/tool-calling

This repairs compatibility and display behavior. It does not guarantee every model response or game-development task will succeed. Incomplete actions remain unexecuted.
