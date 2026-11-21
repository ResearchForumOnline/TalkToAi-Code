# TalkToAi Code 0.3.1 local update — 26 September 2026

This is a local Windows update to the existing 0.3.0 preview installation. It
has not been submitted to Microsoft Store or published on the public site.

## Changes

- The AMD Ollama route checks for the configured model, starts a loopback SSH
  tunnel when the port is free, and retries at startup, every 60 seconds, and
  before Auto/AMD requests. A visible Reconnect AMD model button gives manual
  control. It does not inspect SSH keys or credentials.
- Provider tool results are paired by tool-call ID. Browser tools are offered
  for ordinary internet research requests. A failed project check no longer
  satisfies the agent's post-edit verification gate.
- Gmail and Zmail have optional read-only tools for status, search, and selected
  message/thread reads. Mail tools appear only for mail-related tasks. Mail
  content is untrusted data. No mail send function is exposed.
- The desktop sidebar has Connect Gmail and Connect Zmail controls. Public
  OAuth client IDs can be entered in the UI; tokens stay in Windows Credential
  Manager and are never passed to the agent. Live account sign-in requires the
  user's Google OAuth app and a separately registered Zmail public OAuth client.

## Validation and installation

- Full source suite: 136 tests passed.
- Packaged self-test: passed browser interaction, screenshots, batch file reads,
  output registration, bundled sample and managed-process checks.
- Packaged preview rendered the new controls. A live AMD coding-agent request
  used `read_file` to read a temporary fixture and returned its exact phrase.
- Inno Setup compiled `TalkToAi-Code-0.3.1-Windows-Setup.exe`, then completed
  the local in-place installation with exit code 0. The installed EXE SHA-256
  matched the tested build. The existing task-history file was preserved, and
  the installed app was running after launch.
- Final installer: 66,159,294 bytes; SHA-256
  `11249BC7C6B611AA5B9C850FF39FC6C35A526E4A774FD1D23A66A7DD652EB1A9`.

## Remaining setup

Gmail and Zmail status both report setup needed. No OAuth sign-in or live
mailbox access was attempted. Gmail needs a Google Desktop OAuth client with
the Gmail API enabled. Zmail's operator must register a separate public client
for TalkToAi Code because anonymous dynamic client registration is disabled.
See `MAIL-CONNECTORS.md` for the exact scopes and redirect URI.

Local model and provider quality remains model-dependent. This update adds
tools and reliability but does not claim parity with Codex or ChatGPT Pro.
