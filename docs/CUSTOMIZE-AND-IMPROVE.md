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

Skynet runs the available project check once on a disposable baseline copy
before proposing changes. Each iteration runs its checks in another disposable
copy. Original test files, common check configuration, and `AGENTS.md` are
frozen for candidate selection: edits to those files block selection. A check
run that modifies source in its evaluation copy is also blocked. A passing
candidate is retained when a later iteration fails, so its diff and report
remain available. The report records baseline checks, every iteration's
check status and candidate folder, and which iteration was selected. The code
API accepts 1–5 finite iterations; the desktop button lets you choose one to five.

Without a project evaluator, selection means **latest candidate that passed
the available checks**. It is not an independently measured gain in coding
ability. If no check is available, the candidate is marked unverified and
receives no passing selection. Evaluate gameplay, performance, user experience,
and other quality goals separately before applying a candidate. Tests execute
with the current user's permissions; the copied folder is not an OS sandbox.
A malicious test can still affect files outside that folder if the operating
system permits it.

### Apply or restore a selected candidate

The candidate remains separate until you choose **Apply checked Skynet
candidate** in Evidence. Select its `SKYNET-REPORT.json` entry or use the most
recent report recorded in the conversation. The app shows the selected
iteration, evaluation mode and changed-file list before asking whether to
apply it to the original project. Only a selected iteration with passing
recorded checks can be applied. A metric contract, when present, must still
show a finite improvement over the baseline.

Before writing, the app checks that the original project files still match
their recorded baseline hashes and that candidate files still match their
checked hashes. It rejects protected policy and evaluator files, links,
unsupported paths and changed files. The original bytes are backed up under
`.talktoai-code/candidate-backups/`, with an application manifest that lists
each changed path. Applying a candidate changes project source; it does not
install a new TalkToAi Code build or promote model weights.

Use **Restore last applied candidate** in Evidence to recover the original
bytes. Restore checks that the applied files have not changed since the
application and stops if they have, so later work is not overwritten. The
backup and manifest remain for review. If an application is interrupted, its
manifest records the pending path for manual inspection and bounded recovery.
Review the resulting diff and run the project after applying; passing checks
and a fixed evaluator do not establish broad quality or interactive gameplay.

### Optional measured evaluator

A project can add tracked `SKYNET-EVALUATOR.json` at its root. The contract,
evaluator script, and named fixtures are frozen from the original project.
Skynet runs the existing project checks first, then the evaluator in a
disposable source copy. A candidate is selected only if checks pass and its
finite metric strictly improves over a measured baseline or previous best. If
the baseline check or metric is unavailable, selection pauses because no
improvement can be established. Missing,
malformed, nonfinite, timed-out, or oversized metric output cannot select a
candidate. Without this contract, checks-only selection remains available.

```json
{
  "schema": "talktoai.skynet.evaluator.v1",
  "metric": "mean_absolute_error",
  "direction": "minimize",
  "command": ["python", "evaluation/score.py"],
  "timeout_seconds": 30,
  "frozen_files": ["evaluation/score.py", "evaluation/cases.json"]
}
```

For example, `evaluation/cases.json` can contain fixed local cases:

```json
[{"x": 2, "expected": 3}, {"x": 5, "expected": 8}, {"x": 8, "expected": 13}]
```

An original `evaluation/score.py` can import the project function and print
one JSON object to standard output:

```python
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app import estimate

cases = json.loads((Path(__file__).with_name("cases.json")).read_text())
error = sum(abs(estimate(case["x"]) - case["expected"]) for case in cases) / len(cases)
print(json.dumps({"metric": "mean_absolute_error", "value": error}))
```

The command is limited to a frozen relative Python script, runs for at most
60 seconds, and may emit at most 16 KB. Its output must contain exactly the
named metric and a finite number. Existing project checks must be detectable
for scoring to run. The evaluator is still project code running with your
permissions, and a small fixed case set can be overfit or gamed. Review the
candidate and use separate held-out cases before claiming general improvement.

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

The candidate/evaluator/archive design was informed by the primary
[OpenEvolve project](https://github.com/algorithmicsuperintelligence/openevolve)
and the original [Darwin Gödel Machine code and paper](https://github.com/jennyzzt/dgm).
TalkToAi Code uses its own bounded implementation; it does not copy either
project's source or claim their benchmark results.

## Design references

The editable project-guidance approach follows public patterns documented by
[aider](https://aider.chat/docs/usage/conventions.html) and
[Cline](https://github.com/cline/cline/blob/main/docs/customization/cline-rules.mdx).
These links are references; TalkToAi Code uses its own implementation and does
not require either product.
