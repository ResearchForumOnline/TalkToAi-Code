# Research with an experiment journal

TalkToAi Code can keep a local evidence ledger while you research and run
bounded experiments. Choose **Research an experiment** from Task starters.
The agent can enable the `research` tool set, read the previous entries, use
the existing project commands or managed jobs for an authorized experiment,
and record the outcome before choosing a next step.

Each entry has a stable identifier, UTC timestamp, sequence number, hypothesis,
reported command/result/metrics, evidence filenames with observed sizes and
SHA256 hashes, and a next step. Results and metrics stay explicitly
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
  "next_step": "Repeat the same comparison with seeds 11 and 19 within the agreed budget"
}
```

These are example values, not measurements produced by TalkToAi Code.

## Why this workflow

[Anthropic's 25 September 2026 account](https://www.anthropic.com/research/yes-claude-can-do-nine-loops)
describes a nine-loop six-particle amplitude computation in planar N=4
super Yang–Mills using its Claude Science harness. The guest account includes
Lance Dixon's independent validation and describes following earlier research
methods with continued computation and debugging. Encouragement to continue
was part of a structured scientific workflow with an expert check. It does
not demonstrate that a generic loop or any local model will make discoveries.

For this implementation, only local research-directory and public research
index metadata were inspected. The index's source-ledger and methods headings
informed the separation of reported claims, reproducible commands, and retained
evidence. Private research results, credentials and correspondence were not
used in this document. The feature supports a reusable experiment record;
it does not guarantee scientific discovery or recursive model improvement.
