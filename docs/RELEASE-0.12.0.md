# TalkToAi Code 0.12.0

## Apply and restore a checked source candidate

Skynet Mode still creates a separate candidate and runs the available project
checks. When its report selects a passing iteration, the Evidence panel offers
**Apply checked Skynet candidate**. The app previews the changed-file list and
selected evaluation mode, then asks before touching the original project. It
rechecks original and candidate file hashes, rejects changed or protected
files, saves original bytes in a project-owned backup, and records an
application manifest. **Restore last applied candidate** rechecks applied
bytes before restoring that backup, so later work is not silently overwritten.
An interrupted application leaves a recovery manifest for inspection.

This is an explicit source-copy operation. Checks-only selection means the
detected checks passed. A frozen evaluator can establish a better value for
its declared metric on its fixed cases. Neither establishes overall app,
gameplay, research, or model quality. Review the diff and run the resulting
project before relying on it. See [Customize and improve](CUSTOMIZE-AND-IMPROVE.md).

## Agent-adjustable app preferences

Code tasks can discover `preferences` tools. In Act mode, an agent can change
only the inspected allowlist of future app defaults: automatic context,
tool/model activity display, Keep going, session length, model context size,
browser, search choice and Skynet iteration count. The change is recorded;
the agent can inspect history and request a conflict-checked rollback. Plan
mode can inspect the allowlist and history. A current model call keeps the
options it began with. Permissions, provider and connection profiles,
credentials, model IDs and model weights are outside this tool. See
[Agent changes to app preferences](AGENT-PREFERENCES.md).

## More reliable continuation

For local Ollama inference, a response cut off at a small output limit can
receive a larger allowance on the next response, within the existing context
and step budget. The status line explains when more output is allowed or the
context is too small. This does not extend a task beyond its configured hard
limits or guarantee that the selected model will finish.

A generic **Continue** message uses the last substantive user request when
choosing relevant tools. That matters for a paused mail task: the read-only
Gmail or Zmail tools remain discoverable when the resumed message says only
“Continue”. Each user's account configuration and tool permissions still
apply. No mail-send capability is added.

## Reproducible routing audit from public research

The new read-only `audit_routing_evaluation` research tool compares a baseline
and candidate on the same project-local labelled JSON cases. It reports
false allows, false reviews, deferrals, coverage, paired error changes and
small scenario-family breakdowns. If **every** prediction has an event
probability, it also reports a Brier score and fixed forecast bins. The file
is capped at 1 MiB, 1,000 cases and 16 families; it is byte-hashed and never
executed. The supplied labels, split and probabilities remain unverified
claims, and the result never changes an authorization decision. See the
[format and limits](ROUTING-EVALUATION.md).

This implements a narrow evaluation task from the author's public
[Probability of Goodness routing paper](https://github.com/ResearchForumOnline/research/blob/main/papers/probability-of-goodness-ethical-routing.md):
define the outcome and dataset before judging an agent's routing score.
The paper explicitly says its older keyword score is uncalibrated telemetry,
not a moral probability or decision gate. The public
[Zero Boundary Algebra 1.1](https://github.com/ResearchForumOnline/research/blob/main/papers/zero-boundary-algebra-formal-specification-1.1.md)
also separates claimed states from checked evidence. The audit does not
implement its formal operators or prove general ethical correctness.

## Model-weight work remains separate

The source [training workbench](MODEL-TRAINING-WORKBENCH.md) contains a real
small synthetic adapter-training fixture. Its optional Hugging Face LoRA path
requires a separate installed Python environment, an approved local model
and reviewed data; it was not run on the user's 30B server model. The
observed server VM provides CPU inference and reported zero model VRAM
allocation. This release does not rewrite the served 30B weights, convert a
candidate to Ollama format, or deploy a new model automatically. Training
results and held-out loss need separate capability evaluation.

## Verification and availability

The Windows source suite passed 424 tests with 12 environment/platform skips.
After a final UI wording correction, the focused workspace, Skynet and
candidate suite passed 97 tests with 3 Windows platform skips. The final
Windows package and installed application each passed an isolated release
self-test covering browser interaction, computer-control hook registration,
project evidence, audited preferences and routing audit. The per-user
installer exited successfully; the installed executable matched the final
build, and saved `studio.json`, `config.json` and `connections.json` hashes
were preserved.

Windows installer SHA-256:
`3599fc1c790ecc688f31121b7d8acf37395e7247cbf01384457a578d777db5cb`.
The download includes `SHA256SUMS-0.12.0.txt` for comparison.

The [portable desktop smoke workflow](https://github.com/ResearchForumOnline/TalkToAi-Code/actions/workflows/portable-desktop-smoke.yml)
checks Linux/macOS source installation, offscreen launch and the agent
regressions, including POSIX file-mode checks. The linked release records the
specific run outcome. Offscreen startup does not establish interactive
desktop usability, and these fixtures do not establish general 30B model
reliability or game quality.
