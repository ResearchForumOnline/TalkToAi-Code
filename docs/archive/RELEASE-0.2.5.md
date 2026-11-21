# TalkToAi Code 0.2.5 — Ask for the outcome

This release improves agent follow-through for coding, websites and games. It builds on 0.2.4's recoverable chats, project memory, search, archive controls and bundled example game.

## What changed

- **On-demand tool sets:** the agent can enable additional code-navigation, browser, game, desktop and SSH tools during a turn. Available capabilities are no longer restricted to keywords in the original prompt. Existing mode/access permissions remain in force.
- **Automatic project overview:** with automatic context enabled, the agent gets a bounded read-only overview of the project type, detected checks and available engines without spending a model turn requesting it.
- **Saved task steps:** a multi-step checklist appears in the new Steps tab and survives app restarts. An unfinished checklist prompts the agent to continue or report a real blocker. Checklist completion is explicitly labelled as agent-reported.
- **Bounded automatic continuation:** output-length limits can trigger up to two continuations within the normal turn budget. Incomplete tool batches are not executed. Stop remains available; the app does not run indefinitely.
- **Check evidence beside the plan:** runner-derived statuses distinguish passed, failed, unverified and stale checks. Ordinary shell commands do not count as verifying file edits. This tracks file-tool edits, not every external filesystem change.
- **More resilient tool execution:** invalid argument batches are rejected before any part runs, with bounded model retries. Repeated identical consecutive failures are not executed endlessly.
- **Continue unfinished work:** a button in Steps and an action in Ctrl+K resumes the saved conversation while preserving any existing draft.

## Validation and limits

98 automated tests passed, including on-demand tool availability, permission boundaries, plan persistence, output-limit continuation, retention of the original request during continuation, incomplete-batch rejection, repeated-failure handling and checks derived from runner output rather than model claims. Existing browser/runtime and release tests remain included. The actual Qt layout was rendered using a labelled demonstration profile at two desktop sizes.

A separate read-only acceptance attempt against local `qwen3.5:4b` stopped at its 120-second deadline without producing a tool call. That is **not a live-model automation pass**. The configured localhost server-model endpoint was unavailable during that check. No paid API, credentials or personal project data were used. Quality and latency still depend on the selected model/runtime.

The Windows x64 installer compiled successfully using the existing standard Inno Setup configuration. The final packaged executable passed its runtime smoke check (browser interaction, browser screenshot, vision attachment and bundled example) with exit code 0. Its native Qt preview also launched and exited successfully in an isolated temporary profile. The user's existing installation is deliberately left untouched; an in-place 0.2.5 upgrade on that active installation is not claimed as tested.

## Windows download verification

- File: `TalkToAi-Code-0.2.5-Windows-Setup.exe`
- Size: 66,094,889 bytes
- SHA-256: `277e50fed6a4dc10ae8c539a035a134823dd98ce395fad008c1453941dce7334`
- A matching `SHA256SUMS-0.2.5.txt` is attached to this release.

## Preview status

Unsigned preview. No claim of Codex parity, unrestricted autonomy, complete game playtesting or security certification. Loading tools never grants new permissions. Real credentials, first-time SSH host verification and other necessary user decisions still require the user's involvement. Models and game engines remain separate downloads; optional APIs retain their own prices and limits.
