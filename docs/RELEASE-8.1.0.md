# TalkToAi Code 8.1.0

This update reduces the setup needed to start an ordinary coding or research task. It keeps the user's chosen model and existing Plan/Act, Desktop, browser, mail, and Remote Pilot permissions.

## What changed

- **Read-only tool chains.** The agent can invoke up to six supported project, Desktop-discovery, browser-reading, or remote-reading queries in sequence. Every step uses the existing tool's permission and path checks. The chain validates all steps before executing any, stops on a failed step or cancellation, and returns per-step evidence, skipped steps, byte counts, result hashes, and source-scan limits. It cannot edit files, run commands, connect to a server, control the computer, or access mail. Editing and checks continue through the ordinary Act tools.
- **Find a named project from normal wording.** In Code, “Find my NIGHTFALL game on Desktop” now chooses a unique matching project before the model starts. Discovery scans bounded Desktop/Documents directory names and project markers, then checks a small Godot application-name field when needed to identify the engine root. It excludes hidden, generated, and credential-named folders, and stops for a missing, ambiguous, or incomplete result. An explicit folder path still takes priority.
- **Relevant tools on the first turn.** Clear Desktop, SSH, and mail requests include the needed read tools in the initial model call, including at an 8K context setting. “Check my VPS” can enter the existing Remote Pilot route in Act when that permission is enabled; “use my server model” stays an inference preference.
- **Public current-source checks in Chat.** For explicit current-release questions about Godot, Python, Ollama, and Playwright, the app searches a fixed public query and opens a canonical official page before responding. It never inserts the user's prompt, paths, or mailbox details into that automatic search. “Offline” and private-account questions do not trigger it.
- **Clearer starting points.** The Code screen groups editable prompts under **Help me start** and explains how to ask for a result, inspect tool evidence, and review changed files.
- **Git evidence.** A failed Git status/diff call now reports its exit status rather than presenting its output as a successful change query.

## Scope and limits

Tool chains are query shortcuts, not unattended write pipelines. They have a cooperative 60-second deadline; an in-flight browser navigation may still finish at the browser's own timeout. Public-source preflight covers the four named topics; the ordinary browser remains available for other authorized research. Folder discovery is intentionally shallow and bounded, so a project outside that scope needs an explicit path. Model quality, context length, available hardware, network access, and project-specific checks still determine what work can actually be completed. This update does not make a local model equivalent to a hosted frontier model or guarantee that arbitrary games and apps can be finished unattended.

## Verification

The source regression suite, Windows packaged launch, installer, and portable Linux/macOS smoke outcomes are reported with the published release. A successful packaged launch or offscreen smoke does not establish interactive playability or general task success.
