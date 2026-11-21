# Agent changes to app preferences

TalkToAi Code can expose a bounded app-preferences tool to a Code task. The agent can read the current safe preferences, change them in Act mode, inspect the audit history, and roll back a prior change if the affected values have not since changed. Changes use the same persisted configuration as the Settings dialog. They apply to future requests or new tasks; an in-flight model call keeps the options it started with.

The available keys are `auto_context`, `show_tool_activity`, `show_model_activity`, `keep_going`, `work_session_minutes`, `num_ctx`, `web_browser`, `web_search`, `skynet_iterations`, `ollama_keep_alive`, and `local_worker_limit`. The inspect operation lists their accepted values. “Set local model retention to four hours and allow two project reviewers in my settings” can set `ollama_keep_alive` to `4h` and `local_worker_limit` to `2`. Local Ollama applies the new duration on its next reply. [Model tuning and project reviewers](DESKTOP-9.0.0.md)

Permission settings, desktop and remote access, paid search, provider and connection profiles, credentials, model IDs, downloads, and model weights are outside this tool. The audit file contains only the allowed keys and their allowed values. A broken audit is preserved and further agent preference changes stop until it is reviewed. A rollback refuses to overwrite a later change to the same key.

This is configuration, not model learning. [The training workbench](MODEL-TRAINING-WORKBENCH.md) supports a separate small offline LoRA candidate with reviewed local data; it does not edit a served 30B model, deploy an adapter, or improve a model automatically.
