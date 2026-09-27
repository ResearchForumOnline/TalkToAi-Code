# Live coding acceptance and remaining limits

27 September 2026. Model: `openzero-qwen3-coder-30b-a3b-q3`, through the owner's
Ollama server tunnel. CPU/system-memory execution, 8,192-token context, temperature
0.1 and the agent's 1,536-token response cap. No paid API or project data was used.

## Isolated task

The agent received a disposable project containing `game.py`:

```python
def enemies_for_wave(wave):
    return 3
```

The requested behavior: wave 1 returns 3, each following integer wave adds 2;
reject Boolean values, non-integers and values below 1 with ValueError. The frozen
tests checked waves `(1,2,3,10)` against `[3,5,7,21]`, and invalid inputs
`(0,-1,1.5,True)`. Project instructions permitted implementation changes only.
Desktop/remote filesystem access was disabled for the trial.

## Results, including the failed run

1. The initial source trial used a 10-step budget. After 259.83 seconds it had
   repaired progression but missed Boolean validation. Independent verification
   failed one of two tests. The test file was unchanged. This is not a pass.
2. After the context/recovery changes, a fresh identical fixture used a 12-step
   budget and a 420-second wall limit. The model first made the same error, then
   repaired it after failed-check feedback. Its own checks passed both tests at
   317.47 seconds. It continued rereading/probing and the harness stopped it at
   420.11 seconds before a final answer. Independent verification then passed
   both tests; the test file was byte-identical to its initial state.

The final generated validation was:

```python
if not isinstance(wave, int) or isinstance(wave, bool):
    raise ValueError("Wave must be an integer")
if wave < 1:
    raise ValueError("Wave must be at least 1")
return 2 * wave + 1
```

No human edited the generated implementation. The model used actual read, write,
test and command tools; source changes were checkpointed.

The unchanged test-file SHA-256 was
`368db9d17303ae1208c340408c88c226863d3b6158cea5ab11c80d00b01deb0f`.
The second run completed 11 model responses totaling 1,015 generated tokens;
reported generation rates ranged from 8.09 to 10.31 tokens/second. Counts exclude
the interrupted final response.

## Interpretation

This establishes the named code repair and verification, not clean autonomous
completion or broad game-development quality. The second run's final prose did
not complete. Continued work after passing checks remains a limitation of this
model/workflow. The budgets differed, sampling was not controlled across repeated
runs, and this is not a speed benchmark or a measured general success rate.

The second source trial exercised the context and recovery changes. During it,
one further defect was found: test failures received irrelevant shell syntax
advice. The final release limits that advice to actual PowerShell diagnostics;
the final change is covered by dedicated tests. The packaged release separately
passed tool/runtime tests. A full model-driven coding trial of the final packaged
installer has not been performed.
