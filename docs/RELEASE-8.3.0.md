# TalkToAi Code 8.3.0 — local programs alongside your AI

This update adds useful project work that runs without an LLM, API key, subscription or network request. Open **Local tools** from the workspace, or type one of its exact commands. Custom coding and research continue to use your chosen local, server or API model.

## Local project tools

**Project quick check** combines project orientation, Python syntax findings, TODO comments and Git change names under a shared scan budget. It reports the selected folder and coverage so a partial inspection cannot appear complete.

| Tool | Example in the composer | What it observes |
| --- | --- | --- |
| Project report | `What is in this project?` | Recognized project markers, source languages, likely entry points and check hints |
| Find files | `Find files matching *.gd` | Matching names or relative paths |
| Find code symbols | `Find symbol Player` | Matching declarations in supported source files |
| Find unfinished work | `Show TODOs` | TODO, FIXME, HACK and XXX comments with locations |
| Check Python syntax | `Check Python syntax` | Python parser findings without importing or executing the project |
| Show Git changes | `Show Git changes` | Names and status of staged and unstaged tracked files |

Each tool uses ordinary local code and fixed recognition rules. Scans skip generated trees and known private paths, avoid following links, and impose time, file, byte and result limits. Findings carry partial, cancelled or unavailable status when relevant. Project detection is a navigation aid; syntax success does not prove runtime behavior, and a TODO comment is not necessarily a defect. Git must be installed to inspect Git changes.

## Working starters with editable source

**Local tools → Create a project** offers two fixed templates:

- **Neon Drift** (`browser-game`): an offline canvas arcade game with keyboard/touch controls, dash, score, pause and restart. Open the generated `index.html` directly in a browser.
- **Taskbox** (`python-cli`): a command-line task manager with local JSON storage, priorities, search and completion. Run `python taskbox.py --help` in its folder. It requires a separately installed Python 3.10 or later; the packaged desktop runtime does not provide a general `python` command.

Creation requires Act mode and a fresh child folder. It does not replace an existing folder, install dependencies or launch the generated program. After successful creation the app opens a new task on the project and displays its launch instructions. A failed creation leaves the current task selected. A process interruption can leave a partial new folder; subsequent creation refuses to overwrite it.

New projects open in Code. After creating the browser starter, type **open starter** or **launch game** to open it in your default browser. Local tools suggests an unused starter folder name and preserves an unsubmitted composer draft when running an inspection from the dialog.

## Clear local execution

The workspace distinguishes a running local program from a model request. Local operations start before model selection, retain reports and tool observations in conversation history, support Stop and preserve paused coding checkpoints. Failure guidance describes the local operation instead of asking the user to repair model settings. A broader or compound request keeps the normal agent path rather than silently dropping the coding part.

Source-derived local reports are displayed as literal text, including strings that resemble model tool markup. Opening the Local tools dialog or selecting an item does not inspect a project or create files until the selected action is dispatched.

## Command interface

From a source checkout with Python:

```sh
python -m offline_assistant --project "/path/to/project" --command quick_check --json
python -m offline_assistant --project "/path/to/project" --command project_report
python -m offline_assistant --project "/path/to/project" --command find_files --query "*.py" --json
python -m offline_assistant --project "/path/to/project" --command find_symbols --query Player
python -m offline_assistant --project "/path/to/project" --command python_syntax --json
```

Commands are `quick_check`, `project_report`, `find_files`, `find_symbols`, `todo_report`, `python_syntax` and `git_changes`. The composer also accepts `/local <command>` with a query for the two find commands; `/local` opens the catalog. Reports are printed as text by default or as JSON with `--json`. Exit status 0 means the command completed without reported syntax findings, 1 signals findings, cancellation, missing input or an unavailable operation, and 2 identifies a partial scan. Inspect the report's status and coverage alongside its exit status.

## Verification and release status

- Windows source regression suite: **535 tests run; OK, 13 skipped**, in 73.846 seconds. The skips include the opt-in browser template test. The final 18-test local UI suite also passed after the status wording was refined.
- The separate template browser run passed all **14 template tests**, including actual Edge gameplay controls, score progression, pause/resume, restart and mobile layout, with no JavaScript errors or HTTP(S) requests.
- Local UI tests reject model/server entry points if called and exercise actual template creation in a temporary directory. Source-shaped markup stays visible as local data; local errors, cancellation and paused coding checkpoints retain their meaning.
- Packaged Windows self-test: passed, including `offline_program`, `offline_starter`, existing browser interaction and process checks.
- [Ubuntu and macOS portable install/startup/regression workflow](https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36344360437): both jobs passed at source commit `bd0f20bd5fd6259bf8b1907ee386dbde47366de2`. The later release finalization changes documentation and checksums only. Offscreen startup does not establish interactive usability on every system.
- Installer: `TalkToAi-Code-8.3.0-Windows-Setup.exe`, **66,471,113 bytes**. SHA-256: `222d79c6356b97676238eb7e91b0da0620d5b072006924b0d35712c46e0bac06`.
- Local Windows installation: upgrade exited successfully, the installed binary matches the packaged binary, and its isolated self-test passed. Saved conversation, configuration and connection file lengths and modification timestamps were preserved; private contents were not included in this report.

Download the [8.3.0 release](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v8.3.0) or read the [product page](https://talktoai.org/TALKTOAIcode/). These checks establish the listed behavior and build outcomes; arbitrary project quality still needs task-specific verification.
