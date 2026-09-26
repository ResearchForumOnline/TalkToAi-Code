# Customize your coding agent

Open the actual app or game project in TalkToAi Code and choose Code / Act to
make changes. Task starters include **Upgrade an existing game**, **Customize
this agent**, and **Improve TalkToAi Code source**. A task starter fills the
composer; review its placeholders before sending it.

## Preferences that persist

Open **More → Project instructions · AGENTS.md** to edit project guidance, or edit `AGENTS.md` at the selected project's root.
TalkToAi Code reads this file at the start of a task. Keep it below 24 KB.
The current user request takes priority. Example:

```markdown
# Project guidance

- This is a Godot 4 first-person game. The main scene is scenes/main.tscn.
- Preserve controller and keyboard support when changing gameplay.
- Use existing art first. Record licenses for added assets.
- Implement a playable feature before reporting completion.
- Run the documented headless import check after scene/script changes.
- Report interactive gameplay and frame rate as unverified unless measured.
```

Use **Project memory** for decisions, known bugs and the next milestone. Memory
can become stale; it is context for the agent to check. Never put credentials,
tokens or passwords in either file. Custom instructions influence behavior;
they cannot make a model reliable on every task or give it tools it lacks.

## Improve a game or app

Select the folder containing its manifest (`project.godot`, `package.json`,
`pyproject.toml`, etc.). If starting from a parent folder, name the game and its
path in the request. Give the agent a concrete outcome, such as "add an enemy
wave system with a visible counter and restart flow". Review its changed files
and launch the project to assess the result. Use the 16/32/64-step selector for the task budget. Settings offers 8K/16K/32K model context windows; larger contexts use more memory. If Godot is not on PATH, set **Settings → Godot executable** to its installed executable.

Skynet Mode creates a separate candidate for review. It now preserves Godot
scripts/scenes/resources and supported small game assets, including projects
downloaded as source ZIPs. Git projects use tracked files. Projects without Git
use a bounded scan that skips caches, private directories and generated files.
The limits are 1,500 eligible files, 60 MB total, 4 MB per file, and 20,000 files
examined for a folder without Git. Oversize eligible files stop the copy with a
clear error. Unsupported formats are not copied; complex projects may need a
manually prepared development copy. Binary assets appear in the candidate
report with hashes instead of a text diff.

## Improve TalkToAi Code itself

Download or clone the source from https://github.com/ResearchForumOnline/TalkToAi-Code
and open that source folder as a project. Use **Improve TalkToAi Code source**
with a specific feature or fix. The running packaged app is not a source
checkout. Launch the changed source in a separate development environment
using the repository's installation instructions, then review and check it
before packaging an update. Skynet Mode can create a separate candidate of
this source too; it does not install or merge that candidate automatically.

Candidate checks run with the current user's permissions. A passing syntax,
import or unit check does not establish game quality, performance, or full
product readiness.

## Design references

The editable project-guidance approach follows public patterns documented by
[aider](https://aider.chat/docs/usage/conventions.html) and
[Cline](https://github.com/cline/cline/blob/main/docs/customization/cline-rules.mdx).
These links are references; TalkToAi Code uses its own implementation and does
not require either product.
