# TalkToAi Code 0.3.0 preview — Jobs and deliverables

Windows preview for GitHub Releases and the public website. This unsigned EXE
is not a Microsoft Store submission package.

## New capabilities

- **Managed process jobs:** the agent can start a native executable with literal
  arguments, poll incremental output and cancel the exact job. No command is
  silently rerun by polling. A new native Jobs panel exposes status, output,
  exit code and a Stop selected job action.
- **Explicit lifecycle:** up to three concurrent jobs and twenty per turn;
  deadlines up to thirty minutes, a 64,000-character retained output buffer and
  a 4 MiB output stop limit. All jobs are owned by their agent turn and stopped
  at turn end. Windows Job Objects bound descendant process lifetime.
- **Batch file reads:** inspect up to eight UTF-8 source files with one tool
  call. Bounded results include SHA-256 and continuation offsets. Changed files
  reject stale continuation hashes. One missing file does not hide other results.
- **Deliverables in Evidence:** register reports, build files and other existing
  project outputs with labels, paths, sizes and hashes. Registration never
  uploads or executes files. Double-click reveals registered files' folders.
- **Practical task starters:** build-and-collect and local-website testing prompts,
  alongside expanded in-app help. Tool sets are discoverable during a task.
- **Bring-your-own OpenAI API:** a dedicated preset, model metadata discovery,
  user-supplied session/environment/Windows-encrypted keys, explicit API selection
  and an optional API startup preference. Local/open-weight models stay the
  default. No developer/shared API key, account, subscription or credit is supplied.
- **API controls and visibility:** per-response token ceilings, first-party
  `max_completion_tokens`, usage reporting when supplied, and clearer HTTP errors.
  Token limits are not currency caps. Auto never falls back to a paid API.

## Scope and limitations

Processes use the signed-in account's permissions; this is not a sandbox.
The new job interface has no interactive stdin, does not survive a turn/app
restart and is not a persistent hosting service. Completed job summaries are
saved with the conversation, not reattached to arbitrary system processes.
Exit code zero is not a claim of test coverage or game playability.

Output registration records bytes read at that moment, not an immutable backup.
Models and engines remain separate. No live-model quality/speed improvement is
claimed; the prior local-model timeout is not resolved by controller tests.

The three supplied DevSpace folders were evaluated for capabilities. Their
product licence did not permit code reuse; no DevSpace product code or assets
were copied. See `docs/DEVSPACE-IDEAS-REVIEW.md`.

The OpenAI integration follows current official Chat Completions, function-tool
and model-list documentation. Tests use dummy keys and a loopback HTTP fixture;
no live paid OpenAI request or real credential is used. Model access, pricing,
quota and inference/tool compatibility depend on the user's account and choice.

## Validation

128 automated tests passed. Coverage includes real native command execution,
failure exit codes, cancellation, descendant cleanup after the parent exits,
output flooding, deadlines, turn cleanup, stale file-read hashes, output
registration, chat-scoped job events, own-key API storage, Windows encryption,
no automatic API fallback, metadata-only model discovery and a complete
OpenAI-style tool-call/result stream using a loopback fixture.

The final packaged executable's self-test exited 0: managed process execution,
batch reads, output registration, bundled example, browser interaction,
browser screenshot and image attachment all passed. Its native Qt preview
also launched and exited 0 with a separate temporary profile. Source UI
previews were inspected at 1440x900 and 1100x700, with the provider dialog
rendered in a smaller desktop layout. The packaged tree contained zero
`config.json`, `providers.json`, `studio.json` or `*.dpapi` files.

Windows x64 Inno Setup preview:

- File: `TalkToAi-Code-0.3.0-Windows-Setup.exe`
- Bytes: 66,138,598
- SHA-256: `02c67393cb762ce03479bc4610ce9f061b52435f761ad895e452a9310d43fd57`
- Authenticode status: NotSigned (unsigned preview)
- Matching checksum file: `SHA256SUMS-0.3.0.txt`

The active user installation is not replaced. No in-place upgrade, clean-machine
installation, antivirus clearance or Microsoft Store certification is claimed.
