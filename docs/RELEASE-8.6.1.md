# TalkToAi Code 8.6.1 / Store candidate 1.5.1.0

This is a Windows packaging repair to the 8.6.0 feature update, prepared on 23 October 2026. It preserves Meta/OpenRouter setup, strict free-only routing, user-controlled tools, attachments, exports and the existing Store identity. The earlier 1.5.0.0 candidate and its original WACK report remain preserved as evidence.

## Verified defect and repair

The full WACK run against 1.5.0.0 reported a mandatory DPI-awareness warning: `TalkToAiCode.exe` had neither an embedded PerMonitorV2 declaration nor a statically recognized DPI-awareness API call. Although Qt 6 supplies high-DPI support, the executable's Windows startup contract was incomplete.

Both Windows build scripts now embed the source-controlled [application manifest](../store/TalkToAiCode.manifest). It declares `PerMonitorV2`, a `true/pm` compatibility fallback, `asInvoker` with `uiAccess=false`, and long-path awareness. Windows applies the declaration before Python/Qt creates windows. The Qt runtime retains its native per-monitor scaling; no late DPI override or test-only metadata substitute is used. The packaged self-test queries the actual Windows thread DPI awareness context and requires Per-Monitor V2.

Microsoft documents [manifest-based process DPI awareness](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process). [Qt 6.8 high-DPI documentation](https://doc.qt.io/qt-6.8/highdpi.html) describes its native per-monitor scaling behavior.

## Reproduce without replacing the previous payload

```powershell
.\store\build-payload.ps1 -BuildName 8.6.1 -OutputDirectory "$PWD\dist-studio-store-0861"
.\store\build-msix.ps1 -Version 1.5.1.0 -PayloadDirectory "$PWD\dist-studio-store-0861\TalkToAiCode"
```

The payload and MSIX scripts refuse existing targets. The newer package version identifies changed bytes separately from the uploaded 1.5.0.0 candidate.

## Verified candidate

The focused provider, UI, attachment, update and release suite passed **118 tests** after this repair. The freshly built executable embeds the reviewed manifest and its runtime self-test exited 0 with all checks passing, including the actual Windows Per-Monitor V2 context, browser interaction/popup/screenshot, screenshot attachment, managed process, Escape hook, offline tools and strict free-provider policy. All **55 frozen application modules** match the reviewed source after source-filename normalization.

MakeAppx validation, packaging and unpacking passed. The candidate contains 490 files and no private configuration, API vault, conversations or user projects. Package: `TalkToAi-Code-1.5.1.0-x64.msix`, **106,215,560 bytes**, SHA-256 `9fc5fecac3349ac4fc31d64df7f4c7d48ccefc294c935b856ce324d4c57c54e5`. Its source commit is `9db901f578e85fc06c963edd1c7f4b31b76048e2`. The existing Store identity remains `talktoai.TalkToAiCode-AICodingStudio`, x64, publisher `CN=9B704446-6F62-4D52-954E-775436315422`.

A full Windows App Certification Kit run against this exact package finished on 23 October 2026 with **overall PASS**: **23 tests PASS**, one optional **Blocked executables FAIL**, and no partial run. **Every mandatory test passed, including DPIAwarenessValidation.** This confirms the earlier mandatory DPI warning is repaired. The [sanitized WACK result](WACK-8.6.1.json) records every test and the remaining optional findings without raw host logs.

The optional static check reports process-launch APIs in the executable and bundled Python, Qt, Playwright and Windows bindings, plus blocked-name strings in dependencies and documentation. The app intentionally exposes user-requested process/command tools, but this fact alone does not independently resolve the optional finding. It remains disclosed for reviewer assessment.

The privacy source includes the 8.6 Meta/OpenRouter host and retention supplement; this source change does not establish a live-site deployment. This document establishes a reviewed package candidate and completed local validation. Neither a build nor a WACK run establishes Microsoft certification, public availability or a clean Store-signed upgrade.
