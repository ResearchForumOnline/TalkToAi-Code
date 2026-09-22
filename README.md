# Build and test games TalkToAi Code — Windows desktop preview

> **Latest release: 0.1.6** — [Download the fixed Windows installer](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/download/v0.1.6-preview/TalkToAi-Code-0.1.6-Windows-Setup.exe) · [View release notes and checksums](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v0.1.6-preview)

![TalkToAi Code connects a coding workspace to a game world](assets/talktoai-code-hero.png)

## Product tour

The preview is built around a simple loop: describe the outcome, let the agent inspect and act, steer it when needed, then review evidence.

| Ask and edit | Steer a running task |
| --- | --- |
| ![TalkToAi Code coding workspace with conversation and files panel](assets/screens/studio-chat.png) | ![TalkToAi Code steering an active task](assets/screens/studio-steer.png) |

| Review the result | Build and test games |
| --- | --- |
| ![TalkToAi Code completed coding response](assets/screens/studio-evidence.png) | ![TalkToAi Code Game Lab with Godot and Blender tools](assets/screens/studio-game-lab.png) |

The screenshots are from the Windows preview. They show the conversation workspace, automatic tested coding route, task steering, evidence-oriented responses, and Game Lab controls for Godot, Blender and capture workflows.

TalkToAi Code is a native Windows coding workspace for people who want to build software and games with local or self-hosted AI. The preview is designed around one clear request: describe the outcome, let the agent inspect and act, then review the evidence.

An independent native desktop AI assistant from TalkToAI for coding, games and general project work. Use your local Ollama models, your own SSH-connected Ollama server, or an optional API provider. This is a preview, not an OpenAI product, and no model or subscription is included.

## Install

1. Install Python 3.12 for Windows from https://www.python.org/downloads/windows/ (include the Python launcher).
2. Download and extract the Windows setup ZIP into a permanent user-writable folder.
3. Double-click `Install.cmd`. It creates a local virtual environment, installs the pinned dependencies from PyPI, and creates a **TalkToAi Code Preview** desktop shortcut. Internet and sufficient disk space for the dependencies are required. Setup does not download an AI model.
4. Open the shortcut, select your project, and choose a runtime. Local Ollama defaults to `qwen3.5:4b`; install a suitable model separately or change it in Model choices. A remote runtime needs your own SSH tunnel to localhost port 11435 and model settings. No private TalkToAI server is included.

### Platform installers

- **Windows:** use the [0.1.6 fixed installer](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/download/v0.1.6-preview/TalkToAi-Code-0.1.6-Windows-Setup.exe). It installs the verified runtime under `%LOCALAPPDATA%`, creates Desktop, Start Menu and uninstall shortcuts, and includes an opt-in **Start with Windows** setting that launches to the tray. Version 0.1.6 fixes the shortcut-creation hang from 0.1.5.
- **Portable Windows:** use the [0.1.3 portable ZIP](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/download/v0.1.3-preview/TalkToAi-Code-0.1.3-Windows-Portable.zip) when you do not want an installer.
- **Linux:** install Python 3, extract the source ZIP, and run `bash install-linux.sh`. This creates a virtual environment, a `talktoai-code` launcher, and a desktop entry.
- **macOS:** install Python 3, extract the source ZIP, and run `bash install-macos.sh`. This creates a local virtual environment and a `talktoai-code` launcher.

The Windows native runtime is currently packaged for Windows x64. Linux and macOS use the source installers because native signed builds for those operating systems are not produced on this Windows build host.

## ZeroThink / AgentZero account and vault

Click **Link ZeroThink account**, choose your vault provider and exact model ID, and complete the normal web sign-in and device approval. The desktop remembers its own session using Windows DPAPI. Provider keys remain in the server vault. Do not copy your Google password or vault keys into chat. `link zerothink` also opens the dialog. The existing account/device API is used; no server auth change is required.

The vault adapter is experimental: it requests a structured JSON reply from the selected model and validates the whole tool batch before execution. Invalid replies fail without executing that batch. Model tool reliability varies. Local/AMD routes use native Ollama tool calls. The adapter's mock protocol, Windows token encryption and URL checks were tested; a real signed-in vault inference has not yet been verified. Account entitlements and provider quotas still apply. APIs are not guaranteed free; no paid provider is selected automatically.

Use your account's device controls to revoke access. Closing the linking dialog cancels polling; an already issued session may need revocation in the account.

## Working

- Act permits project edits and commands. Plan is for inspection. Commands run with your signed-in Windows account permissions.
- Desktop / user access and PC Pilot can operate accessible Windows controls. Browser tools use a separate Edge session, not personal cookies. Close unrelated sensitive windows before screenshots.
- File-tool edits are checkpointed. Shell/SSH changes do not receive the same automatic rollback.
- Ask for a reviewer, investigator or test planner subagent. Up to two workers run sequentially per turn, five model steps each. They inspect local project files; the main agent makes changes.
- Checks detect Godot import, Python, package scripts, Rust and .NET. Unity uses project-specific commands. Blender and Godot must be installed separately.
- Closing the window normally hides it in the tray; choose Quit to exit. Stop cancels the active run. Detached remote processes may outlive SSH cancellation.
- F1 opens help; Ctrl+K opens actions. Steer lets you redirect work during a task.

## Privacy and limits

Task history is stored locally under `%LOCALAPPDATA%/TalkToAiCode`. Selecting an API or remote model sends task context and tool results to that endpoint; review the scope of files you ask it to inspect. The source ZIP includes no credentials, personal server profiles, saved tasks, model weights or private configuration. No automatic updater is installed. This app does not guarantee coding accuracy, full game playtesting, or parity with any hosted assistant.

## Development

Install `requirements-desktop.txt` and run `python -m unittest discover -q`. Start with `python studio.py`. Build a Windows folder executable using `Build-Studio.ps1`; distribution of dependency binaries requires their applicable notices and source/licensing obligations. The published setup ZIP contains this project's source and installs third-party libraries normally through pip.

Original application source: MIT, see LICENSE. Dependencies retain their own licenses; see THIRD-PARTY-NOTICES.md.

Website: https://talktoai.org/TALKTOAIcode/
Source/releases: https://github.com/ResearchForumOnline/TalkToAi-Code
