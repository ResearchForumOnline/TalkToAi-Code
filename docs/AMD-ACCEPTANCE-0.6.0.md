# Live server acceptance exercise — 0.6.0

On 26 September 2026, a real local model repaired two isolated Python behavior
fixtures through TalkToAi Code's source agent loop. The model's own `run_checks`
passed all four frozen tests. An independent final run also passed all four.
No human edited the implementation after the exercise began, and the test
file's SHA256 stayed unchanged.

## Runtime and result

- Model: `openzero-qwen3-coder-30b-a3b-q3:latest` through Ollama on the owner's
  AMD server, reached through a local SSH tunnel.
- Context: 8,192 tokens; output cap: 1,536 tokens per response; temperature: 0.1.
- Ollama reported `size_vram: 0`; this exercised CPU/system memory execution.
- Initial run: 12 model responses, 331.96 seconds. It fixed wave progression
  and volume normalization but paused with the boolean validation case failing.
- Continuation: the saved user/assistant/tool history was loaded through a new
  source-agent process, with the same model and context. It completed 14 more
  model responses over 443.47 seconds before the runner was stopped.
- Total elapsed across both runs: **775.43 seconds** (about 12 minutes 55 seconds).
- Completed model responses: **26**. Reported generated tokens: **1,979**.
  Sum of completed model-request wall times: **763.19 seconds**. Observed
  generation rates ranged from **7.12 to 9.46 tokens/second**.
- The model reproduced failures, inspected implementations/tests, edited both
  implementations, recovered from a rejected exact-text edit, identified the
  Python `bool`/`int` edge case, repaired it, and ran passing checks.
- After its checks passed, the model continued redundant inspection/probes.
  The runner was deliberately stopped to free the server; final model prose
  was not completed. The independent post-stop test run passed.

Generated-token counts cover completed responses only. These timings include
model loading/prompt processing and tool interaction; they are not hardware
benchmarks or a matched comparison with another model. Source repairs were
being integrated during the exercise; this is evidence for the exercised
source workflow, not an acceptance test of the final packaged installer.

## Reproduce the fixture

Create an isolated folder with these two intentionally broken implementations:

```python
# game.py
def enemies_for_wave(wave):
    return 3
```

```python
# app.py
def normalized_volume(value):
    return value
```

Save this as `test_behavior.py`:

```python
import unittest
from game import enemies_for_wave
from app import normalized_volume
class BehaviorTests(unittest.TestCase):
    def test_game_wave_progression(self):
        self.assertEqual([enemies_for_wave(n) for n in (1,2,3,10)], [3,5,7,21])
    def test_game_rejects_invalid_wave(self):
        for value in (0,-1,1.5,True):
            with self.assertRaises(ValueError): enemies_for_wave(value)
    def test_volume_clamp(self):
        self.assertEqual([normalized_volume(v) for v in (-20,25,120)], [0,25,100])
    def test_volume_converts_numeric_text(self):
        self.assertEqual(normalized_volume('42'),42)
if __name__ == '__main__': unittest.main()
```

The exercised Windows fixture used UTF-8 with CRLF line endings and a final
newline. Its test-file SHA256 before and after was:

```text
ec6b504c95be11edd220091b59b7b5171908e4f49fe08a478766c8b7966aa74b
```

Open that folder, select Code / Act and the server model, and request:

> Fix both implementation bugs in this selected isolated project. In game.py,
> enemies_for_wave must return 3 enemies for wave 1 and add 2 enemies per
> subsequent wave; reject non-integers, bools and waves below 1 with ValueError.
> In app.py, normalized_volume must convert numeric input/text to a number and
> clamp it to 0..100. Inspect source and existing tests, edit the implementations
> without changing tests, run_checks, and fix any failures. Finish with actual
> verification evidence.

The fixture's root `AGENTS.md` also said to edit implementation only, keep
`test_behavior.py` unchanged, run `python -m unittest discover -v`, and stay
within the isolated project. Desktop/remote filesystem tools were disabled.
The server was used for inference; source files stayed on the local machine.

Independently run `python -m unittest discover -v` and compare the test-file
hash afterward. Final observed result: `Ran 4 tests`, `OK`, exit code `0`.

## What this establishes

The model executed actual tools, made source edits and debugged toward passing
checks after automatic context compaction and a saved-history continuation.
It also showed slow execution and unnecessary repeated inspection. This
small Python fixture does not establish full game creation, graphical quality,
Godot playability, production app readiness, or parity with a frontier model.
