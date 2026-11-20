# TalkToAi Code 8.7.0 desktop guide

## Set up a model

Agent Zero runs the native TalkToAi Code tool loop. Start with **Local** and send a request. If Ollama is installed, TalkToAi starts it as needed and downloads OpenZero 1.7B if it is missing. This is a 1.1 GB download and can be stopped or disabled in Settings. **Model downloads → Install Qwen3.6 35B A3B** installs the Ollama Q4 GGUF variant and selects **Local large**. The Qwen model is about 23 GB on disk. It uses about 3B active parameters per token and still needs memory for its 35B total weights and the chosen context.

**Save Qwen UD-Q4_K_M GGUF** saves the pinned Unsloth quantization for a compatible external llama.cpp runtime. The Hugging Face picker can list files by repository and revision, save individual files, and use a read token for private or gated models the account can access. Compatible GGUF weights can be imported into Ollama. Other formats can be downloaded for a compatible runtime.

[Qwen model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) · [Unsloth GGUF](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) · [Ollama Qwen build](https://ollama.com/library/qwen3.6:35b)

## Control a browser or a Windows app

For a browser task, select **Act**, then ask e.g. “Open Brave, go to http://localhost:3000, fill the project name field, and check the result.” In **Settings**, select a hidden browser, a visible managed browser, or a connected Chromium debugging session. For Chrome, Brave, and Edge connected mode, choose the browser, use **Open connectable browser**, and navigate or sign in manually inside that browser window. TalkToAi can inspect the connected tabs, switch between them and operate page controls. Managed and connectable browsers use their own profile. Your regular browser window can be controlled through Windows accessibility when it exposes its controls. [Chrome debugging profile guidance](https://developer.chrome.com/blog/remote-debugging-port)

For a regular Windows window, enable desktop access and PC Pilot in **Settings**, use **Act**, then identify the target in the prompt, e.g. “Use PC Pilot to inspect the open Notepad window.” Text models can operate accessibility controls. A vision model can use fresh screenshots for window-relative input. Use **Stop** or **Escape** to cancel an active computer-control task.

## Use Blender

Install Blender on the PC. Auto-discovery checks PATH and common install locations. For a portable or custom installation, choose **Settings → Blender executable** and browse to the executable. **Open Blender** launches the application. In an **Act** project, ask e.g. “Use Blender to create a low-poly prop, save it as `prop.blend`, reopen it, and verify the scene.” The Blender tool can run a project script in the background, optionally loading a project `.blend` first. Long renders can run as jobs and be checked in **Jobs**.

## Install game assets

Open **Game assets** from the Game Lab or Actions. Choose only the packs the project needs, then click **Install selected**. TalkToAi verifies the original ZIP against its catalog and extracts assets and license notices under `assets/talktoai-library`. Import a previously downloaded original ZIP by selecting its corresponding entry and clicking **Import verified ZIP**. The optional offline collection includes the original ZIPs and their licenses. [Kenney asset licensing](https://kenney.nl/support)

## Set up OpenAI

Open **Private API vault**, choose **OpenAI**, enter your API key, fetch models and select an available model. The preset supports the Responses API and Chat Completions. Use Responses for current models that require it for tools. Inference uses the selected provider and may incur that provider’s charges. Store keys using the app’s credential vault. [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)

## Add tools through MCP

Use **Actions → Integrations and MCP plugins** to configure an installed MCP stdio server. The dialog includes a filesystem-server example. Each entry has a `command`, an `args` array, an `enabled` flag, and an optional `pass_env` array of environment variable names. Choose a server you trust and scope its arguments to the files or services you want to expose. A plugin executes with your account’s permissions. The configuration is user-owned and shared across projects. A project file or a chat message cannot install or enable a plugin. In **Act**, ask the agent to use the plugin by name for the intended operation.

Project Data is built in and reads project CSV/TSV previews and SQLite schemas. For example, ask “Inspect the schema of `app.sqlite` with Project Data.”

The native Agent Zero implementation follows OpenZero’s observe, act and verify workflow and calls for inspection when an interruption leaves an action outcome unknown. It uses OpenZero’s local model and borrows the scoped session and observed-control approach from Tab Pilot. Model qualification checks for actual structured calls through Ollama, following ResearchForumOnline’s serving-path research on template failures. Task-scoped browsers and optional tool discovery also draw from Hermes. [AgentZERO project](https://github.com/ResearchForumOnline/AgentZERO) · [OpenZero run architecture](https://github.com/ResearchForumOnline/OpenZero/blob/main/openzero/docs/ARCHITECTURE.md) · [OpenZero Tab Pilot](https://github.com/ResearchForumOnline/OpenZero/blob/main/browser-extension/README.md) · [Serving-path benchmark](https://github.com/ResearchForumOnline/research/blob/main/papers/local-llm-serving-path-benchmark-2026-07-26.md) · [Hermes tools](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/tools-reference.md) · [MCP stdio specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
