# Live goal-continuation acceptance

Model: `openzero-qwen3-coder-30b-a3b-q3` on the existing loopback server tunnel, with an 8K configured context. No cloud API was used.

## Fixture

A disposable local project contained a JSON file with run B17 and measurements 3, 5 and 8. A goal with one pending criterion was saved through the conversation store, reloaded, and supplied to the agent with a short continuation request. The expected sum was 16. This was a small read-only tool-orchestration task, not a reasoning benchmark or scientific experiment. Desktop and remote tools were disabled for the trial.

## First run

The model listed files, read the measurements and loaded goal tools. It first used an invalid `completed` criterion status, received a validation error, then corrected it to `met` with the right file reference and sum. The fixture remained unchanged. Elapsed time was 209.81 seconds.

This exposed an application recovery defect: the corrected metadata error still prevented a completed checkpoint. The app reported unfinished work rather than hiding the error. The repair distinguishes outstanding goal-validation errors from actual file, command and test failures. A valid corrected goal update cannot clear unrelated failures.

## Follow-up run

The real model reloaded the saved goal, read the measurement file, updated the criterion with sum 16 and its source reference, and reached Ready with a completed checkpoint. The source fixture was unchanged. No timeout or cancellation occurred. Elapsed time: 151.11 seconds. The trial still included a corrected goal-status validation error; the repaired recovery behavior allowed it to finish.

An independent check of the report confirmed the saved-goal round trip, unchanged fixture hash, expected sum/source reference and final Ready status. This tiny task taking about two and a half minutes also shows that server/model latency remains a practical limitation. It is not a broad intelligence or coding-quality benchmark.

Criterion status and evidence references remain model-reported even when independently compared with this fixture.
