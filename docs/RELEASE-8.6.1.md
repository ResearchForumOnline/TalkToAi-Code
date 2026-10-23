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

The focused provider, UI, attachment, update and release suite passed **118 tests** after this repair. Fresh packaged-runtime, embedded-manifest and WACK results are recorded when the repaired candidate is verified. The prior optional static blocked-executable finding remains disclosed; the app intentionally exposes user-requested process/command tools. Neither a build nor a WACK run establishes Microsoft certification, public availability or a clean Store-signed upgrade.
