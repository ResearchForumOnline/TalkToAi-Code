# Project workbench and playbooks

Open **More → Project workbench** (or use Actions) after selecting a project.
The workbench has three views:

| View | What it shows | What it changes |
| --- | --- | --- |
| Task | Saved task objective, checkpoint, blockers and observed source changes | Nothing; **Prepare to continue** places an editable request in the composer |
| Playbooks | Project-owned workflow steps and the current status of their attached evidence files | **Use selected workflow** places an editable request in the composer |
| Experiments | Reported metrics, protocol fields and current evidence status | **Compare** calculates a read-only reported difference |

The workbench itself does not run a saved command, test or agent action. Send
the prepared request when it matches the current task. Act mode is required to
save a playbook or experiment; finding and comparing records work in Plan.

## Save a workflow after it works

Ask the agent to **save a project playbook** after it has completed a repeatable
task and inspected actual check output. A playbook contains a title, when the
steps apply, one to twelve concrete steps, verification notes and up to eight
project-relative evidence files. The files are hashed and the record is saved
under `.talktoai-code/PLAYBOOKS.json` in that project. This action only records
guidance. It never executes the steps or grants new permission.

At retrieval, the app compares current evidence-file bytes with the recorded
size and SHA-256. `current` means those bytes match; `needs_review` means a
file changed or could not be checked; `no_evidence` means none was attached.
Matching bytes do not prove the steps still work, that checks passed, or that
the workflow is appropriate for a new request. Review the current project,
test commands and authority before using it. Keep credentials out of titles,
steps and verification notes; text filtering is best effort. Evidence paths
reject common private locations and links outside the project.

## Compare experiments without inflating claims

The [experiment journal](RESEARCH-EXPERIMENTS.md) records reported numeric
metrics alongside dataset, split, seed, environment, controls, budget, metric
definition and sample size. Source claims can be linked to up to four source
URLs and labelled `supports`, `contradicts`, `background` or `unverified`. The
label is the recorder's assessment; the journal does not fetch the page.

Choose two records with the same metric in **Experiments** and specify whether
lower or higher is desirable. The comparison reports arithmetic and warns
when conditions differ, protocol fields are missing, a limitation is recorded
or current evidence files no longer match. It does not run an experiment,
assess significance or validate a scientific claim. Reproduce and inspect
the underlying work before publishing a result.

## What the source-change count means

TalkToAi checks a bounded inventory of eligible source files in the selected
project at task start and after actions that may edit them, including shell
commands. It can therefore notice changes outside its file editor and mark a
previous check stale. The counter and changed-path list cover only eligible
files within that scan. Large, generated, private, linked, remote or otherwise
unreadable files may be outside the scope. A partial scan is explicitly shown
as partial. Use Git changes and direct file inspection to review the full
project, especially before a release.

## Reviewed source improvement

Skynet Mode creates a separate candidate source copy. Choose one to five
iterations; each iteration has a bounded agent run. The initial source and
check configuration are frozen for comparison. Detected checks run in a
disposable evaluation copy, and a later failed iteration does not displace the
latest passing candidate. The JSON report includes baseline and candidate
check status, changed files, selected iteration and diff path. Review that
report and the actual source before manually applying anything. Checks run
with the signed-in user's permissions. A passing project check is not a
measured improvement in quality, and Skynet does not replace the original
project or rewrite model weights.

## Research basis

[Anthropic's original account of the nine-loop computation](https://www.anthropic.com/research/yes-claude-can-do-nine-loops)
describes a sustained scientific workflow with computation, debugging and an
independent physicist's check. Repeated encouragement to continue was one part
of that workflow. [SWE-agent's trajectory documentation](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md)
separates saved run traces and configuration from later evaluation. This
workbench applies those evidence distinctions to local project work. Its code
is original and does not include modules from those projects.
