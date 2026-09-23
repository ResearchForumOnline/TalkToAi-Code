# Microsoft Store submission status (2026-09-23)

TalkToAi Code - AI Coding Studio was **submitted for Microsoft Store certification** on 2026-09-23. Partner Center showed **In certification**, with pre-processing in progress. It is **not yet certified or publicly available**. Publishing is on **manual hold**: even if certification passes, it will not go live until the publisher selects **Publish now**.

## Product and package

- Store product ID: `9PPGFB30SHD9`
- Product identity: `talktoai.TalkToAiCode-AICodingStudio`
- Publisher identity: `CN=9B704446-6F62-4D52-954E-775436315422`
- Submitted package: `TalkToAi-Code-1.3.0.0-x64-r3.msix` (x64, Windows 10 2004 or newer)
- SHA-256 of submitted local package: `18D2E13D608AE5FE8D75890AF3C38217DF0592B7BB8F8D3884FFBEC238F17B7A`
- Partner Center accepted the package and displayed **Validated**; pricing, properties, age ratings, package, listing, and submission options displayed **Complete** before submission.
- Store link after publication: https://apps.microsoft.com/detail/9PPGFB30SHD9
- App privacy: https://talktoai.org/TALKTOAIcode/privacy/
- App support: https://talktoai.org/TALKTOAIcode/support/

## Verification performed and remaining

The source test suite passed (130 tests, 1 skipped). The rebuilt PyInstaller EXE self-test exited 0. Windows registered the staged unpacked manifest as a developer package and launched its process side by side with the running direct EXE. The final MSIX was built with MakeAppx and contains the manifest and payload but not the generated self-test report. Partner Center validated the uploaded package.

These checks **do not prove that the Store-signed MSIX installs, upgrades, uninstalls, or operates correctly for a fresh user**. The local file is unsigned; Windows App Certification Kit could not be run in the non-elevated shell. Before selecting **Publish now**, obtain Microsoft's certification result, install the Store-signed build on a clean Windows profile/device, check launch and core tools, uninstall/reinstall, and confirm no user data loss. Record the results here. Do not call the app Store-published while certification or the manual hold remains.

The Store edition has a separate state directory (`%LOCALAPPDATA%\TalkToAiCodeStore`) and single-instance channel from the direct EXE. Store updates are Store-managed. Existing direct-install task history is not automatically copied into the Store edition.

The separate EXE/MSI draft `TalkToAiCode` was deleted in Partner Center after explicit user confirmation because it was the wrong package route and blocked MSIX name reservation. No other Store product was deleted. The exact name `TalkToAi Code` remained unavailable immediately afterward, so the reserved Store display name is `TalkToAi Code - AI Coding Studio`.

Official Microsoft references:

- [MSIX package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements)
- [Manual desktop MSIX packaging](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion)
