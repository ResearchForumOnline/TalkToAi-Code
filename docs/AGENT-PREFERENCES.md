# Agent changes to app preferences

TalkToAi Code can expose a bounded app-preferences tool to a Code task. The agent can read the current safe preferences, change them in Act mode, inspect the audit history, and roll back a prior change if the affected values have not since changed. Changes use the same persisted configuration as the Settings dialog. They apply to future requests or new tasks; an in-flight model call keeps the options it started with.

The available keys are `auto_context`, `show_tool_activity`, `show_model_activity`, `keep_going`, `work_session_minutes`, `num_ctx`, `web_browser`, `web_search`, and `skynet_iterations`. Each has a fixed set of accepted values shown by the inspect operation. For example, "set my future Code sessions to four hours and show model activity" can change `work_session_minutes` to `240` and `show_model_activity` to `true`. More time or context can make a model slower or use more compute.

Permission settings, desktop and remote access, paid search, provider and connection profiles, credentials, model IDs, downloads, and model weights are outside this tool. The audit file contains only the allowed keys and their allowed values. A broken audit is preserved and further agent preference changes stop until it is reviewed. A rollback refuses to overwrite a later change to the same key.

This is configuration, not model learning. [The training workbench](MODEL-TRAINING-WORKBENCH.md) supports a separate small offline LoRA candidate with reviewed local data; it does not edit a served 30B model, deploy an adapter, or improve a model automatically.
