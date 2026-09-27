# TalkToAi Code 0.11.0

## A project workbench

**More → Project workbench** brings three existing kinds of work into one
place. **Task** shows the current saved objective, checkpoint, blockers and
observed source changes. **Playbooks** searches project-owned workflow notes,
including steps, verification notes and current evidence-file status.
**Experiments** lets you select a baseline and candidate record, choose a
shared metric and inspect the reported difference and its limitations. Using a
playbook or continuing a task first places a request in the composer; opening
the workbench does not execute it. See [Project playbooks](PROJECT-PLAYBOOKS.md).

## Better change and research evidence

- A bounded source inventory observes eligible changes in the selected project
  after file and shell actions. If source changes after a passed check, that
  check is marked stale and the agent gets a chance to verify current files.
  The UI distinguishes a complete scan from a partial one. The count is scoped
  to observed source files; Git and direct review remain useful for full diffs.
- Playbooks retain up to twelve steps and eight safe evidence references.
  Current size and SHA-256 are compared on retrieval. Matching hashes establish
  current file bytes, not a successful workflow.
- Research records can include dataset, split, seed, environment, controls,
  budget, metric definition, sample size, limitations and explicit source
  claims. Comparing two records warns about missing or differing conditions,
  uncertain claims and changed evidence. Values remain reported measurements;
  the comparison does not prove causality, significance or a discovery.

## Candidate source improvement

Skynet Mode now offers one to five bounded candidate iterations. It records
baseline check output, freezes the initial source and check files, and runs
detected checks in a disposable evaluation copy. Without a project evaluator,
it retains the latest candidate that passed checks, including when a later
iteration fails. With a tracked, frozen `SKYNET-EVALUATOR.json` contract, it
also runs the project's metric command and selects a checked candidate only
when its finite metric strictly improves on the baseline or previous best.
The report records the evaluation mode, baseline and selected metrics when
available, selection reason, check outputs, changed files and diff. The
original project is not replaced. Checks and the evaluator execute with the
signed-in user's permissions. Checks-only success is not a measured gain in
quality; a metric result supports only the stated metric under that evaluator.
Review the diff and tests before applying a change yourself.

## Bounded live coding fixture

One disposable coding fixture ran with the configured 30B server model for
258.3 seconds and reached eight tool steps. The agent changed one source file
to fix an inclusive upper bound, reran the checks, and both existing tests
passed with their test files unchanged. It saved a project playbook, then
paused at the eight-step limit. The runtime reported zero bytes of model VRAM
allocation. This is a small fixture result, not a GPU performance measurement,
game-scale evaluation, or evidence that every coding task will succeed.

## Defaults and limits

New Code conversations start with **Keep going** selected and a two-hour
maximum. Existing saved settings and each conversation's choice are
preserved. Work can finish or pause earlier because of the step/pass budget,
errors, repeated discovery, missing evidence or Stop. New Windows
installations keep the existing desktop access and PC Pilot defaults; native
desktop control is Windows-specific. The selected model, hardware, project
checks and available tools still determine what the agent can finish.

This release adds inspectable workflows, not automatic research validation,
unbounded autonomous execution, or model-weight training. The source and
platform packages remain separate from user credentials, projects and model
weights.

On Windows, a vision-capable model can use a point click tied to a fresh
screenshot of the same inspected window when accessibility controls are
insufficient. The screenshot expires after 30 seconds or a window move or
resize. Text-only models continue to use observed accessibility control IDs;
clicking a point never by itself proves the target action succeeded.

## Inspiration and implementation

The [original Anthropic nine-loop account](https://www.anthropic.com/research/yes-claude-can-do-nine-loops)
describes continued computational work and independent validation. The
[SWE-agent trajectory documentation](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md)
shows why a saved run and configuration should remain distinct from evaluation.
TalkToAi Code uses original application code and does not copy those agents or
claim their results.

## Verification

- Windows regression suite: 390 tests run, 8 skipped, no failures.
- Packaged Windows self-test passed: operating policy verification, Escape
  hook registration, managed process execution, bundled sample, browser
  interaction and popup, screenshot and image attachment, batch reading,
  output registration, playbook storage, research comparison, and source
  change evidence.
- The Workbench tabs were rendered and inspected using example fixture
  records. Product tour images label those records as examples.
- The bounded live model fixture is described above. It is separate from
  deterministic regression tests and does not establish broad agent parity.

Installer checksums and the Ubuntu/macOS workflow result accompany the
published [0.11.0 release](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v0.11.0).
