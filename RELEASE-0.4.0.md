# TalkToAi Code 0.4.0 preview — Chat, Code and reviewable improvement

Windows x64 GitHub preview. This installer is unsigned and separate from the
Microsoft Store submission. It includes no model, subscription or account.

## What changed

- Separate Chat and Code spaces preserve conversation type across restarts.
  Chat starts in read-only Plan mode; users can explicitly switch to Act. Pin,
  archive, branch, search and move conversations without discarding history.
- Refreshed dark teal desktop UI, clearer project actions and a prominent
  launcher for the separately installed Cline companion.
- Opt-in Skynet Mode makes a bounded candidate copy of eligible project source,
  asks the selected model for up to two focused improvement passes, runs
  detected project checks and records a reviewable diff and JSON report.
  It does not replace the original, merge changes or publish anything.
- Optional read-only Gmail and Zmail connectors offer mailbox status, search
  and selected message/thread reads after separate OAuth client setup and
  user sign-in. No mail send tool or shared credential is included. Tokens are
  stored in Windows Credential Manager.
- AMD model tunnel recovery runs at startup, every minute and before Auto/AMD
  requests. Coding-agent provider tool results are paired by call ID, ordinary
  internet research requests expose browser tools, and failed checks do not
  satisfy the post-edit verification gate.

## Verification

- Source automated suite passed: 141 tests.
- Packaged self-test passed managed jobs, batch file reads, output registration,
  bundled sample, browser interaction/screenshot and image attachment checks.
- The packaged 0.4.0 UI rendered with a disposable empty profile. The public
  screenshot demonstrates controls, not model quality or connected mail.
- A live AMD coding request used `read_file` and returned an exact phrase from
  a temporary fixture. A live Skynet run edited a separate temporary Git
  candidate while leaving the original unchanged. That fixture had no detected
  project checks, so its check status is **unverified**.

## Boundaries

Skynet candidate checks execute with the signed-in Windows user's permissions;
the temporary copy is not an OS sandbox. Git projects copy tracked eligible
source files; non-Git folders use a narrow source allowlist. File-name filters
cannot prove that source contains no private data. Review the candidate diff,
check output and any changed tests before manually applying work.

Gmail needs a Google Desktop OAuth client with Gmail API enabled. Zmail's
operator must register a separate public OAuth client because anonymous
registration is disabled. Neither live mailbox sign-in was verified for this
release. The selected model can make mistakes and will see the mail content
the user asks it to read. See `MAIL-CONNECTORS.md` and the privacy page.

Design referenced local research about a model/wrapper/tools/verification
boundary and deterministic recursive adaptation examples. Those sources are
inspiration, not proof of autonomous self-improvement or outside endorsement.
No Aider, Cline, OpenHands or DevSpace product code was copied into this build;
the existing Cline launcher opens a separate installed app.

## Installer

- Asset: `TalkToAi-Code-0.4.0-Windows-Setup.exe`
- Bytes: 66,175,285
- SHA-256: `193F2C2AE3AE06B1F2F6E52454761839AB2673C37E38FC5F5FAF7ED8657DB8AA`
