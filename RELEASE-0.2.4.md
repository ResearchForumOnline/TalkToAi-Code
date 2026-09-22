# TalkToAi Code 0.2.4 — A clearer, more resilient workspace

Build websites, software and games with your own local or self-hosted model, or an optional API provider. This release focuses on finding your work, keeping it recoverable and getting started with less setup.

## New in this release

- **Full-text chat search:** find titles, projects, drafts and conversation text. Ctrl+Shift+F focuses chat search; Ctrl+F finds text in the current conversation.
- **Archive and restore:** Active, Archived and All chat views keep the sidebar tidy without deleting history. The chat menu and right-click menu offer rename, pin, branch, archive/restore, copy last reply and export report.
- **Chat recovery:** atomic saves retain a validated previous-save backup. At startup, damaged history is preserved under a unique filename and a valid backup is recovered with a visible notice. No model call is involved.
- **Seven editable task starters:** coding improvements, website features, game features, debugging, release review, SSH inspection and continuing unfinished work. Starters never execute automatically or overwrite an existing draft.
- **A properly bundled example game:** Game → Open example game creates a writable Score Arena copy in persistent app data. Existing example-project edits are preserved. Godot is a separate prerequisite.
- **A slimmer sidebar:** More groups project memory, API providers, ZeroThink linking, model choices and help. Settings, Connections and About & updates remain directly accessible.
- **Reliability fixes:** pinned chats no longer steal selection from new/branched conversations; chat-list refresh preserves unsaved file-editor text; local model choices persist in user settings.

Includes editable project memory, autosaved drafts, GitHub update checks and SHA-256 verified installer downloads, saved SSH alias discovery, task steering, file diffs/checkpoints, browser tools, desktop tools and Game Lab from earlier releases.

## Install or update

Download `TalkToAi-Code-0.2.4-Windows-Setup.exe` below, or open **About & updates** in the app. This is a standard Windows x64 per-user Inno Setup installer with bundled Python runtime, Start Menu and optional Desktop shortcuts, and a Windows uninstaller. Existing chat history and settings remain in `%LOCALAPPDATA%\TalkToAiCode` during upgrades. Updates are user-initiated, not silently installed.

## Validation and limits

The full 78-test suite passed, covering agent/tool protocols, project checks, memory, SSH discovery, provider integration, update verification, computer-tool mocks, real Edge interaction/screenshot fixtures, session corruption/recovery, bundled-example copying and desktop UI regressions. The actual Qt layout was rendered and reviewed at 1280×800 and 1600×960 using a disposable demo profile.

The final installer completed with exit code 0 on the build machine. Desktop and Start Menu shortcuts resolve to the installed executable; Windows registers version 0.2.4 and its uninstaller. The installed runtime passed its smoke check (exit 0): bundled example present, browser interaction successful, screenshot saved, and image attachment encoding working. This checks packaging and browser behavior, not model quality or full gameplay. Installer size: 66,078,972 bytes. SHA-256: `16e3c5ddd007d1afc18b47bf9ea567d67a0b9bf79fc28c5873006d74b7977899`.

Windows preview; installer unsigned. Tests are not a security certification or proof of complete game playtesting. Chat recovery retains the previous successful save, not every historical version, and is not a substitute for project or off-device backups. Archived chats remain on your machine. Screenshots of the new layout use a clearly labelled UI demonstration, not a benchmark or live model result.

Models and game engines are separate downloads. CPU inference is supported, with speed/quality dependent on hardware and model. Optional APIs keep their own quotas and prices. Remote models receive the task context you send them; do not store passwords in chat or project memory. ZeroThink vault inference remains experimental. Linux/macOS remain source installations; this release does not claim native signed installers for them or parity with hosted assistants.
