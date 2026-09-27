# TalkToAi Code 0.9.0

## Repair the observed stalled workflow

The reported NIGHTFALL task had paused after 32 steps. Its saved activity contained
31 tool calls: seven file listings, twelve commands (nine failed), and ten reads
(three missing paths), plus project information and search. No file-tool edits or
successful verification were recorded. Shell commands can change files outside
the file-tool ledger; zero tracked edits is not proof of no filesystem changes.

- Optional file globs filter discovery before the output limit. On the actual
  project, `*.tscn` returned `main.tscn`; `*.gd` returned 54 source paths instead
  of a long screenshot and asset listing. This inspection changed no game files.
- Explicit Windows PowerShell instructions and recovery hints address CMD-style
  `dir /s`, unsupported `&&`, and unquoted paths containing spaces.
- Failed or successful shell commands no longer reset unchanged-discovery
  detection. No command is silently rewritten and no failed task is called complete.
- Optional tool groups can be loaded on demand to keep the default 8K context
  usable after adding operating instructions.
- Small 8K coding tasks now use a concise core prompt and load optional browser,
  mail, game extras and delegation schemas on demand. The latest unresolved
  failed-check evidence is retained when older history is compacted, with a
  bounded recovery instruction to diagnose the failing case and preserve tests.

## Progress and runtime diagnostics

- Live phase, model step, elapsed time, tool count, tracked edits and time since
  the last update. Silence is described as waiting, not proof of a deadlock.
- Paused conversations show recorded checks, blockers and a concrete next action.
  Previously saved pauses receive a computed report without rewriting history.
- Completed response metrics expose measured loading, prompt processing and
  generation time when Ollama supplies them. Missing measurements stay absent.
- Read-only model diagnostics distinguish an unloaded model, unavailable
  residency data, CPU/system-memory execution and reported GPU allocation.

The owner's server currently exposes six Ryzen 5900X vCPUs and a virtual display
adapter. A live probe reported zero model VRAM, 14.6 GiB model allocation and an
8,192-token context. Loading took 26.35 seconds. This identifies the observed
CPU execution path, not a universal speed benchmark. No model download, GPU
purchase, VM hardware change or inference-provider switch was performed.

## Readable, research-informed operating policy

The policy explicitly permits ordinary coding, research and authorized security
testing. Fixed-digest verification, direct policy-write protection and Skynet
candidate checks are implemented; the rest of the application remains editable.
No keyword topic ban, invented ethical probability or license restriction was
added. See [the complete boundary and research basis](https://github.com/ResearchForumOnline/TalkToAi-Code/blob/main/docs/OPERATING-POLICY.md).

## Verification

- **332 tests passed on Windows**, with native desktop and localhost browser acceptance enabled.
- The final packaged executable passed policy verification, Escape registration,
  managed commands, bundled sample, batch reading, output hashing, browser
  interaction, popup following, screenshot/image attachment and GUI startup.
- Linux/macOS source installation, offscreen startup and regression checks passed:
  https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36283940852
- A real server-model trial repaired a disposable coding fixture and passed both
  independent tests without changing the test file. It reached passing checks at
  317.47 seconds, but continued work and hit a 420-second harness limit before a
  final answer. The earlier trial failed. These limitations are retained in the
  [live coding evidence](https://github.com/ResearchForumOnline/TalkToAi-Code/blob/main/docs/LIVE-CODING-0.9.0.md).

This release improves concrete tool behavior and visibility. It does not establish
AGI, ChatGPT parity, universal coding success, or fast CPU inference. No model
weights or operating-system privileges were changed.
