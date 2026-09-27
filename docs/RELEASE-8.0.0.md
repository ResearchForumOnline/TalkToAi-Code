# TalkToAi Code 8.0.0

Release name: **TalkToAi Code 8.0**. The three-part version `8.0.0` keeps the updater and installer compatible.

## What changed

- **Outcome-first Code tasks.** For relevant game, website, and app requests, the controller searches current public documentation before the first model response. Queries are fixed topics: user messages, local paths, file names and credentials are never copied into an automatic search. Search results are source leads, and the agent must open a source before citing content. Automatic search uses free browser engines rather than the optional paid Serper key. Say `offline`, `no web`, or `do not search` to skip it. A failed search does not stop local coding.
- **Desktop project discovery.** Asking to find or check a Desktop project triggers a bounded read-only scan of project manifest names in Desktop and Documents. It does not read project file contents or visit credential directories. The agent still needs to inspect the selected project before editing, and ambiguous matches need your choice.
- **Plain-language starting points.** New starters cover finding a project, making a playable game from an idea, and using a saved SSH server alias. The empty Code screen shows example requests and explains Act, Plan, and offline mode.
- **Visible preflight.** Automatic browser search and desktop discovery appear in Tools with their actual result or failure before the model begins work.

## Access and limits

Desktop discovery requires Desktop / user access in Settings. Remote edits require Act mode, a specific user request, Remote Pilot access and a working OpenSSH alias. Passwords and private keys are not read into the model context. Project and tool results sent to a remote/API model may leave the computer according to the selected provider.

This release reduces setup and tool-discovery friction. It does not supply a model, guarantee that a game is complete, prove scientific claims, or equal a hosted frontier service. Review diffs, checks, and interactive gameplay separately.

## Verification

Source regressions, packaged startup, the Windows installer and portable Linux/macOS smoke checks should be read from the associated release workflow and checksums. A successful package smoke does not prove interactive use on every system.
