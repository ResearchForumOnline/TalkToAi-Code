# TalkToAi Code 8.8.0 on Windows

## Get a model running

Open **Models and setup → Recommended**. Choose **Install OpenZero 1.7B** for the 1.1 GB default, or **Install Qwen3.6 35B A3B** for the roughly 23 GB model. Keep the model window open for progress through runtime startup, download, verification and selection. The Ollama preset installs weights directly into its model store. Use **Save Qwen UD-Q4_K_M GGUF** if you want a standalone file for another runtime.

Both presets run on this PC. When Ollama is missing on Windows, the install button uses Windows Package Manager to install it for the current user. If your PC lacks WinGet or your organization blocks it, **Get Ollama** opens the official installer. After installation, select **Check Ollama connection** and retry your chosen preset. **Local large** selects Qwen after a successful installation. Three billion active parameters still require storage and sufficient working memory for the full weights. [Ollama for Windows](https://docs.ollama.com/windows) · [Qwen model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)

Under **Hugging Face**, paste a repository, folder or file URL and press Enter. A file link selects the matching file. Review the selection, then click **Download selected files**. Use the import checkbox for an Ollama-compatible GGUF, including a complete set of supported split files. Other model formats can be saved for their respective runtimes. Private and gated files require a read token with access.

Under **Installed Ollama**, select the exact model and choose **Remove from Ollama…**. The confirmation names the model. Standalone Hugging Face exports live in a separate directory accessible from **Open download folder**.

## Use an open browser or Windows app

In **Settings**, enable desktop access and PC Pilot for Windows control. Select **Act** and ask, for example, “Use my already open Brave window and read the heading on this page.” A connected Chromium session can inspect its tabs directly. For ordinary open windows, PC Pilot uses Windows accessibility and observed controls. For an isolated session, choose a managed browser. Stop or Escape interrupts control. [Browser and desktop guide](DESKTOP-8.7.0.md)

## Use game assets on this PC

Open your project in Studio and choose **Game assets**. Select a pack and install it. The Windows installer and Store package carry all 25 original Kenney packs, so importing a pack works offline. Unity projects receive files under `Assets/talktoai-library`. Godot projects receive files under `res://assets/talktoai-library`. Each pack includes its source and license.

Try “Find a platform prop in my installed Kenney assets and use it in this scene.” For Godot, choose a compatible GLB and import it into your project. For Blender, use the project-relative asset path. Unity projects can use the FBX assets and the installed Unity Editor. Studio Settings supports executable overrides for custom engine installations. [Kenney asset licensing](https://kenney.nl/support)

Agent Zero, the OpenAI Responses and Chat Completions options, other provider presets, Project Data and MCP plugins are also available. [Provider and plugin setup](DESKTOP-8.7.0.md)
