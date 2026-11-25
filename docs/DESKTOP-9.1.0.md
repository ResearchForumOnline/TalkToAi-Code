# TalkToAi Code 9.1.0

## It keeps going

Agent Zero no longer stops to wait when it has a question. It writes the question in the chat, carries on with its best judgement and avoids anything destructive while it is unsure. Reply at any time to steer it, or press **Stop**. To make it wait for each answer instead, turn off **Keep working when the agent has a question** in Settings.

A long task no longer pauses after a fixed number of steps while it is still making progress. Each Effort level allows a number of automatic extra passes, and the run still stops at your work-session limit.

## Effort: Low, High or Max

Choose **Effort** beside the step budget. It sets how much the agent spends on each request: reply length, attempts at a requested file, automatic continuation of a cut-off reply or file, command time limits and, for API models, reasoning effort.

| Effort | Best for |
|---|---|
| Low · fastest | Quick answers on slow hardware. Short budgets. |
| High · default | Everyday work. Continues by itself while it is making progress. |
| Max · best result | Big files and hard tasks. Largest budgets and most retries; slowest, and uses the most tokens on paid APIs. |

A reasoning level saved on an API profile always takes precedence.

## Files and 3D scenes arrive, or you are told exactly why not

- A file the model writes is accepted as JSON, as JSON inside a code block, or as a plain code block. A file cut off by the output limit is kept and the model is asked to continue from the last complete line, instead of starting again.
- A tool call that runs out of output while writing a file is switched to a file reply that can be continued. Nothing is written from a cut-off call.
- A Python syntax error names the file, line, column and the line itself. Blender errors are reported with the failing script line and a hint.
- A 3D request without Blender no longer stops. Blender is installed automatically when **Install missing tools automatically** is on. Without Blender the scene is still delivered as a checked GLB file with a viewer page that opens in your browser and a preview image.
- If the model cannot write a working Blender script, it describes the scene as simple shapes and the app builds it. If that also fails, a built-in design that matches the request is delivered, and the reply says so.

## Installs and languages

With **Install missing tools automatically** on (the default), the agent can install Blender, Godot, a C/C++ compiler, CMake, Ninja, Git, Node.js, Python, FFmpeg and Rust with the system package manager: winget on Windows, Homebrew on macOS, Flatpak, Snap or Homebrew on Linux. Only these fixed package names are used. Each install is shown in the activity bar and logged in `install-log.jsonl` in the app's data folder.

- **C and C++**: `build_native` compiles and runs a single source file or builds a CMake, Meson or Make project. Edited C and C++ files are syntax-checked with the installed compiler, and CMake projects run their CTest tests.
- **Scripts**: `run_script` runs a Python, Bash, PowerShell or Node.js script without leaving it in the project.
- A project command may run for 3 minutes at Low, 10 at High and 30 at Max. Installs and builds always get at least 15 minutes.

## The activity bar

A small bar above the message box shows what the agent is doing now, such as *Running: cmake --build build · 42s* or *Installing blender*. Hover over it to see the last actions.

## Models

- **Claude API**: choose **Claude API (Anthropic)** in the API vault and add your own key. Claude Opus 5.5 is suggested; Sonnet 5.5, Haiku 5.5 and Fable 5.2 are in the list. Effort sets how much the model thinks, including **Max**.
- **OpenAI**: GPT-6.2 Sol is the default and GPT-6.2 Astra is in the list. A saved GPT-6.1 Sol profile shows a note.
- **Muse Spark**: the Meta Model API preset (`muse-spark-1.3`) is now labelled **Meta Model API (Muse Spark)**.
- **Local models**: Models and setup offers Qwen 3.5 4B, OpenZero Gemma 4 E4B, Qwen 3.5 9B and Qwen 3.8 27B. Qwen 3.8 has no official small version; its smallest is 27B (about 18 GB).
- Inkling 2 Mini replies can be three times longer, and the app warns before loading it on a computer that is unlikely to have enough memory.

## Auto-tune

With **Auto-tune local models** on (the default), the app reads this computer's graphics memory, memory and processor cores once and fits each local model: the largest context that is estimated to fit, processor threads when part of the model runs on the processor, and the sampling values its makers publish for Qwen models. An Ollama server that the app starts gets flash attention and an 8-bit context cache, which roughly halves the memory a long context needs. Your own Ollama environment settings always win. Type **/tune** to see what it found and chose.

A slow local model is not cut off by a timeout: the wait for a reply follows that model's measured speed.

## Chat

- “Try search the web for …” and “google …” now search the web, as “search the web for …” already did.
- A reply cut off by the length limit is carried on automatically (up to three times at High) and arrives as one message. The end of a long reply stays in view, so “continue” works after it.

## Packages

Version 9.1.0, Microsoft Store package 1.10.0.0. New standalone Linux (tar.gz and AppImage) and macOS (.app in a .dmg) builds come from `scripts/build-desktop-app.py`, and the **Release apps** workflow builds all platforms. See [RELEASE-9.1.0.md](RELEASE-9.1.0.md).
