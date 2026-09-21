# 0.1.0 preview

Initial public source-based Windows setup release. Native Qt desktop, local/SSH-tunnelled Ollama, optional compatible APIs, experimental ZeroThink device login and vault adapter, checkpointed editing, browser/computer tools, source navigation, game checks, steering and bounded read-only subagents.

46 automated tests passed in the development environment, including Windows DPAPI session protection and strict account-adapter tool parsing. No end-to-end signed-in ZeroThink model request or clean-machine installation has been verified. This is a preview for testing, not a stable or signed installer. Browser, desktop and provider compatibility varies; see README.

No models, personal task history, API keys, server aliases or private configurations are included. Dependencies are installed by pip from PyPI during setup. Runtime/model/provider access must be configured by each user.
