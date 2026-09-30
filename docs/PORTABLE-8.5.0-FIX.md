# TalkToAi Code 8.5.0 portable-source repair

This is a focused Linux/macOS source repair, distributed separately as `v8.5.0-portable.1`. The reviewed Windows installer, portable binary archive, Store package 1.4.0.0 and immutable `v8.5.0` tag retain their reviewed bytes. The app runtime version remains 8.5.0. This repair does not rebuild or replace those Windows binaries.

## Verified failure

[Portable smoke run 36745316281](https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36745316281) installed and opened the application successfully on both operating systems. Ubuntu then ran 443 regression tests and reported two errors and five skips. Both errors occurred before the loopback HTTP request: API key lookup required a secure desktop keyring even for an unauthenticated local/self-hosted endpoint. Headless Ubuntu had no secure credential-store backend. macOS completed the same 443 tests successfully with five skips.

## Repair and credential boundaries

On Linux/macOS, a profile pointing to exact `localhost`, `127.0.0.1` or `::1`, or explicitly marked `self_hosted`, may use no-key transport when a secure system keyring is unavailable. A usable secure keyring still supplies a saved authentication key for those endpoints. This supports unauthenticated local compatible servers without requiring unrelated desktop credential services.

Session or environment keys retain their existing route. Unmarked external profiles and cloud profiles still report an unavailable secure credential store when no session/environment key is supplied. The repair never reads a plaintext/null/failed backend and never enables remembering a key in such a backend. Remote keys still require HTTPS. Direct OpenAI and Groq profiles still require a key. Windows DPAPI lookup, encryption, Store/direct key scopes and key-storage behavior are unchanged.

Seven new credential tests cover exact loopback hosts, explicitly marked self-hosting, missing/insecure keyrings, rejected unmarked external and cloud routes, session-only keys, preserved secure-store authentication and rejected insecure key storage. A real loopback HTTP test simulates the missing secure keyring, verifies successful completion and confirms that no Authorization header or insecure-store read occurs.

## Verification status

- Focused API/provider/document tests: **33 passed** locally, including all eight new regression tests.
- Full local source suite: **568 discovered, 555 passed and 13 skipped** in 82.613 seconds.
- [Replacement Linux/macOS workflow 36747048491](https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36747048491): **both jobs passed** at source-repair commit `2d883550968e307566643cde3f3c41bb03181808`. Each operating system ran **451 tests: 446 passed and five skipped**. Ubuntu completed the regression step in 12.229 seconds; macOS completed it in 52.499 seconds. Install and offscreen application startup passed on both platforms. The original failed run remains available as evidence.
- These checks do not establish a clean-device Windows installation, live API allowances or an installed-app upgrade.

## Downloads

- [Reviewed Windows 8.5.0 release](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v8.5.0)
- [Repaired Linux/macOS source release](https://github.com/ResearchForumOnline/TalkToAi-Code/releases/tag/v8.5.0-portable.1)
- [Repaired Linux/macOS source archive](https://github.com/ResearchForumOnline/TalkToAi-Code/archive/refs/tags/v8.5.0-portable.1.zip)

The portable-source release is a stable repair with its own exact-source checksum; it is not a preview and does not become the Windows app's automatic-update target.
