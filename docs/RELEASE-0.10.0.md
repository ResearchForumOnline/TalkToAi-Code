# TalkToAi Code 0.10.0

## Live work is easier to understand

The reported NIGHTFALL screenshot showed a continuing run at model step 24,
with 22 tool calls and a model response 33 seconds earlier. It did not show a
task error. The narrow Tools view made long source output difficult to read,
and the model could spend time loading or generating before a visible reply.

- **Request received** appears immediately after Send. This is an app receipt;
  the selected model has not replied at that point.
- The run card, model step, elapsed wait and latest activity keep updating while
  the app waits for inference. Stop and Steer remain available.
- The Tools panel uses horizontal scrolling for long command and source output
  instead of wrapping code into a cramped column.
- The edit counter reports changes recorded through TalkToAi's file tools.
  Shell commands and external editors can modify files without incrementing it;
  inspect Git changes when determining what actually changed.

## Optional model activity

Use **More → Show model activity** before a new request. For a supported local
or server Ollama model, this requests its separate reasoning stream and displays
live activity in the **Model activity** tab. The stream is model output, not a
complete account of the model's internal process. A short excerpt is bounded
and filtered for common credential patterns and tool markup; filtering cannot
guarantee that every sensitive string is removed. The excerpt is not saved in
conversation history. The option is off by default and can increase latency and
token use. API providers do not receive this setting; a model without a separate
stream continues with the normal status display.

## Bounded long coding sessions

Code's **Keep going** offers one, two or four-hour maximum sessions. Work remains
bounded by at most twelve passes of the selected 16, 32 or 64-step budget, up to
768 model/tool cycles. A task may complete earlier or pause when its time or
step budget is reached, tool errors remain unresolved, discovery repeats, or a
pass produces no new evidence. The app saves checkpoints for a later Continue.
The session runs while the desktop app remains open; it is not a background
scheduler. CPU-only server inference can be slow, and a longer time allowance
does not ensure that a model completes a complex game upgrade.

## Limits and verification

Before release, the Windows source suite passed 340 tests (8 skipped), the
packaged runtime self-test passed its browser, desktop-hook, managed-command,
file, and policy checks, and an isolated packaged workspace rendered in
offscreen mode. These checks do not establish interactive game quality or
long-session reliability on every model. The active 0.9.0 NIGHTFALL run was
left untouched; this version takes effect when the user installs it later.

These controls report observed activity and provide more time to work; they do
not prove the game's quality, verify a shell-edited file automatically, or make
every model support reasoning. Check the task's actual file diff, builds and
gameplay evidence before treating a result as complete. Platform downloads and
published checksums are listed on the
[0.10.0 release page](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v0.10.0).
