# TalkToAi Code 0.6.0

This release repairs project targeting and long-task context failures reported during a NIGHTFALL game request.

## Changes

- Resolve explicit workspace paths and named app/game projects before model inference; require selection when matches are ambiguous or discovery is incomplete.
- Honor embedded `use server always` (or a personal name such as AMD) as inference routing, separate from local or remote project access.
- Condense older exchanges, retain the current objective, original task excerpts and valid recent tool batches, and preserve full saved conversation history.
- Load optional tools as needed so desktop, SSH and mail schemas do not consume the entire default context.
- Read large files in small offset-checked pages and protect edits with exact matches/checkpoints.
- Offer 16/32/64 task steps, 8K/16K/32K context settings, and an editable project AGENTS.md with change guards.
- Select a Godot executable in Settings for project import checks and launching.
- Preserve supported Godot/Unity source and small assets in Skynet candidate copies, including nested source ZIPs.
- Public Server naming with a customizable personal display name and editable model IDs.
- Compare installed server/large-local models with bounded code/tool fixtures; no downloads or automatic route changes.
- A deterministic unchanged-discovery guard provides recovery guidance and an explicit unfinished pause for persistent loops.
- Remove a duplicated legacy installer block on Linux.
- Display the app version in its window title and keep source/staging installations from intercepting the installed application's launch.

- Opt-in Keep going continues useful unfinished work for up to three bounded passes, with saved progress checkpoints and recovery after verified repairs.
- Research experiment journal records hypotheses, reported results, evidence hashes and next steps using bounded atomic storage.
- Fix native Windows button activation using UI Automation; verified against an isolated real Windows form.

## Verification

- 221 automated regression checks passed on Windows.
- Packaged Windows app: browser interaction/screenshot, managed process execution, batch reads, output registration, bundled example and image attachment checks passed; GUI launch exited successfully.
- Ubuntu and macOS source installation, offscreen GUI startup and portable agent regressions passed: https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36274863285
- The reported NIGHTFALL request resolves to the intended native Godot project using directory/manifest metadata.

- Real server-model coding trial: [fixture, checks, timings and limitations](AMD-ACCEPTANCE-0.6.0.md). Both implementation bugs were repaired and all four frozen checks passed.

- Installed 30B server versus 27B local comparison: [measurements and limits](MODEL-COMPARISON-0.6.0.md).

## Scope

These checks do not establish complete game quality, interactive Linux/macOS behavior or parity with a hosted frontier assistant. The selected model determines reasoning and coding quality. Context condensation is an approximation and can require rereading source. Source-based customization and Skynet candidate changes require review; they do not alter model weights or automatically replace a running application.

## Design research

- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://aider.chat/2023/10/22/repomap.html
- https://aider.chat/docs/usage/conventions.html
- https://www.anthropic.com/research/yes-claude-can-do-nine-loops

The implementation remains native to TalkToAi Code. These are design references, not bundled app dependencies.
