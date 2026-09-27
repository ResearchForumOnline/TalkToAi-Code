# Research with an experiment journal

TalkToAi Code can keep a local evidence ledger while you research and run
bounded experiments. Choose **Research an experiment** from Task starters.
The agent can enable the `research` tool set, read the previous entries, use
the existing project commands or managed jobs for an authorized experiment,
and record the outcome before choosing a next step.

Each entry has a stable identifier, UTC timestamp, sequence number, hypothesis,
reported command/result/metrics, optional method protocol, evidence filenames
with observed sizes and SHA256 hashes, and a next step. Results and metrics stay explicitly
**self-reported**. Hashing a file establishes its bytes at that time; it does
not independently verify a measurement or scientific conclusion. Command text
in an entry is metadata: recording an entry never executes it.

The journal lives at `.talktoai-code/EXPERIMENTS.jsonl` inside the selected
project. `read_experiments` works in Plan and Act; `record_experiment` requires
Act. Writes use a lock and atomic replacement. Damaged journals are preserved
for repair. The bounds are 200 records, 1 MiB total, 12 KB per record, up to 12
finite numeric metrics, and up to 8 evidence files of 4 MiB each. Reads return
at most 20 recent entries within a bounded response. Archive a full journal
manually before starting another; the tool never silently deletes history.

Keep secrets out of commands, result text and metrics. Common credential paths,
private folders, missing files and paths outside the project are rejected as
evidence. Evidence contents are not copied into the journal.

## A useful iteration

1. State a hypothesis and what observation would disprove it.
2. Define a baseline, metric, data split/seed, and finite time or run budget.
3. Run the authorized command and preserve its output in a project evidence file.
4. Record actual failures as well as improvements. Keep measured values separate
   from interpretations, and note missing controls or uncertain results.
5. Compare the result to the baseline and select one justified next experiment.
6. Stop at the budget or a real blocker. Independent replication or specialist
   review is a separate step before making a research claim.

Example tool arguments:

```json
{
  "hypothesis": "Caching repeated inputs reduces runtime without changing outputs",
  "command": "python benchmark.py --seed 7",
  "result": "Reported runtime decreased; outputs matched this fixture. More seeds remain.",
  "metrics": "{\"runtime_seconds\": 2.3, \"output_mismatches\": 0}",
  "evidence_paths": "[\"results/seed-7.csv\", \"results/seed-7.log\"]",
  "next_step": "Repeat the same comparison with seeds 11 and 19 within the agreed budget",
  "protocol": "{\"dataset\":\"fixture v1\",\"split\":\"held-out A\",\"seed\":\"7\",\"environment\":\"Python 3.12\",\"controls\":\"same inputs and configuration\",\"budget\":\"20 seconds\",\"metric_definition\":\"runtime measured in seconds\",\"sample_size\":\"10 cases\",\"limitations\":\"Only one seed\",\"source_claims\":[{\"claim\":\"Earlier paper described this method\",\"url\":\"https://example.org/method\",\"relationship\":\"unverified\"}]}"
}
```

These are example values, not measurements produced by TalkToAi Code.

## Compare a baseline and candidate

`compare_experiments` is read-only in Plan and Act. Supply the two IDs returned
by `record_experiment`, the exact metric name in both records, and whether a
smaller or larger value is desirable:

```json
{
  "baseline_id": "BASELINE_ID",
  "candidate_id": "CANDIDATE_ID",
  "metric": "runtime_seconds",
  "direction": "minimize",
  "verify_evidence": "true"
}
```

It reports the candidate minus baseline difference and whether that numeric
change points in the requested direction. The result remains **reported
arithmetic**. It also warns when dataset, split, seed, environment, controls,
budget, metric definition or sample size are missing or differ, when a record
reports limitations or uncertain source claims, when the
candidate predates the baseline, or when attached evidence no longer matches.
Current file hashes are rechecked by default within a 32 MiB total read budget.
`comparable_as_reported` means only that the listed protocol fields agree and
the attached file bytes currently match. It does not validate the measurement,
statistical significance, causality, source URLs or scientific conclusion.
Missing evidence yields a warning; no artifact is invented or downloaded.

The optional `protocol` field is bounded to dataset, split, seed, environment,
controls, budget, metric definition, sample size, limitations, at most four
HTTP(S) source URLs and at most four claim-to-source notes. Each note labels a
claim as `supports`, `contradicts`, `background` or `unverified`; that label is
still the recorder's assessment. Source URLs are syntactically checked but are
**not opened or authenticated** by the journal.
Use the browser research tools to read original sources and record what each
one actually supports. Older records without protocol metadata remain readable;
their comparison will show the missing fields rather than imply comparability.

## Recheck evidence before resuming

Ask the agent to read recent experiments with `verify_evidence` enabled:

```json
{"limit":"5","verify_evidence":"true"}
```

The read-only check rehashes the referenced project files and adds a temporary
`evidence_check` to each returned entry. It never modifies the journal, executes
the recorded command, or updates the old hash to hide a changed result. The
response includes the check's UTC time and the number of evidence bytes read.

| File status | Meaning and next step |
| --- | --- |
| `match` | Current size and SHA256 match the recorded file bytes. |
| `changed` | The file has different bytes; inspect the new result or rerun the original experiment before relying on it. |
| `missing` | The recorded file is absent; restore the original artifact or repeat the experiment. |
| `blocked` | The recorded path is outside the project, sensitive, or a link. It was not read. |
| `invalid_record` | The record lacks a valid path, size or hash. Preserve and repair the journal entry. |
| `budget_exceeded` | This call reached its bounded verification budget; request fewer recent entries. |
| `unreadable` / `unverifiable` | File permissions, size limits or a file changing during the read prevented a reliable comparison. |

An entry is `all_match` only when every attached evidence file matches. Entries
with no attachments are `no_evidence`; missing evidence never counts as a pass.
Reads check at most 32 MiB across the selected entries, prioritizing the most
recent records, with the existing 4 MiB per-file cap. Returned checked entries
are limited to 32,000 characters. Ordinary reads keep their existing smaller
response limit and do not read evidence contents unless explicitly requested.

Matching hashes support reproduction by identifying unchanged artifacts. They
do not validate scientific conclusions, authenticate a journal that someone
could have edited, prove a command executed, or establish measurement quality.
Preserve the command, environment, inputs, seed and independent checks needed
to reproduce the actual result.

## Why this workflow

[Anthropic's 25 September 2026 account](https://www.anthropic.com/research/yes-claude-can-do-nine-loops)
describes a nine-loop six-particle amplitude computation in planar N=4
super Yang–Mills using its Claude Science harness. The guest account includes
Lance Dixon's independent validation and describes following earlier research
methods with continued computation and debugging. Repeated encouragement to
continue was part of that workflow. A generic loop or local model does not
inherit its result. The [SWE-agent trajectory documentation](https://github.com/SWE-agent/SWE-agent/blob/main/docs/usage/trajectories.md)
separates saved run traces and configuration from subsequent evaluation. The
journal applies the same evidence boundary to its own reported experiments;
it does not import SWE-agent or its code.

For this implementation, only local research-directory and public research
index metadata were inspected. The index's source-ledger and methods headings
informed the separation of reported claims, reproducible commands, and retained
evidence. Private research results, credentials and correspondence were not
used in this document. The feature supports a reusable experiment record;
it does not guarantee scientific discovery or recursive model improvement.
