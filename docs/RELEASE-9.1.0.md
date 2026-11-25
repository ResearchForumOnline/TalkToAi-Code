# Releasing TalkToAi Code 9.1.0

App version **9.1.0**. Microsoft Store package **1.10.0.0** (9.0.x shipped as 1.9.x.0, and the Store requires a higher version). Product `9PPGFB30SHD9`, identity `talktoai.TalkToAiCode-AICodingStudio` and publisher `CN=9B704446-6F62-4D52-954E-775436315422` are unchanged.

## Build

The **Release apps** workflow (`.github/workflows/release-apps.yml`) runs the unit tests on Linux, then builds each platform on its own runner. Start it from the Actions tab, or push a `v9.1.0` tag. Each job uploads its packages as a workflow artifact.

| Platform | Output | Built by |
|---|---|---|
| Windows | `dist-store/TalkToAi-Code-1.10.0.0-x64.msix`, `dist-installer/TalkToAi-Code-9.1.0-Windows-Setup.exe` | `store/build-payload.ps1`, `store/build-msix.ps1`, Inno Setup |
| Linux x86_64 | `TalkToAi-Code-9.1.0-Linux-x86_64.tar.gz` (run `install.sh`) and `.AppImage` | `scripts/build-desktop-app.py linux` |
| macOS | `TalkToAi-Code-9.1.0-macOS-arm64.dmg` | `scripts/build-desktop-app.py macos` |

The source kits from `scripts/build-portable-kit.py` still work and are still tested by **Portable desktop smoke**.

To build by hand, install the runtime requirements and PyInstaller on the target platform, then run the same commands as the workflow.

## Sign before publishing

- **Microsoft Store**: upload the MSIX in Partner Center. The Store signs it. Run the Windows App Certification Kit on the package first.
- **Windows installer**: sign `TalkToAi-Code-9.1.0-Windows-Setup.exe` with your code-signing certificate, or SmartScreen will warn users.
- **macOS**: the workflow signs ad hoc only, so Gatekeeper blocks the app on other Macs. Set `MACOS_SIGN_IDENTITY` to your Developer ID Application identity when building, then notarize and staple the DMG with `xcrun notarytool submit … --wait` and `xcrun stapler staple`. An Intel build needs an Intel macOS runner.
- **Linux**: no signing is required. Publish the SHA-256 of each file with the release.

## Store listing and certification notes

Update the listing's "What's new" from [DESKTOP-9.1.0.md](DESKTOP-9.1.0.md). Add to the reviewer notes:

- **Installs**: with *Install missing tools automatically* on (the default), the app can install developer tools the user's task needs (Blender, a C/C++ compiler, CMake, Git, Node.js, Python, FFmpeg, Rust, Godot, Ninja) through the Windows Package Manager, using fixed package IDs. Installs are shown while they run, logged, and can be turned off in Settings. Reviewers may ask that this be described in the listing; the Settings description is the place to point to.
- **Questions**: the agent notes a question and carries on by default; the user can steer or stop at any time, and can turn this off in Settings.
- **Access**: full access (desktop, files and tools as the signed-in user) remains the default, as in 9.0.x. Remote commands, credentials, payments and final external submissions are still not automated.
- **Claude API**: like the other API providers, it uses only the user's own key.

## What was verified for this release

Verified in the development environment (Linux, Python 3.13, no Qt):

- The unit tests for the agent, file and scene pipeline, providers (OpenAI Responses and Claude Messages against local HTTP fixtures with dummy keys), chat, effort, auto-tune, installs (simulated) and C/C++ builds with real GCC, Clang and CMake.
- Generated Blender scripts executed against a stand-in `bpy` module; GLB files read back; viewer pages contain no network references.
- The main window, Settings, API vault and Models dialogs were constructed with a stand-in for Qt to catch errors in the new code paths.

Not verified, and to check before publishing:

- The Qt user interface itself, including the activity bar, Effort selector and new Settings, and the UI test modules (they need PySide6).
- PyInstaller, MSIX, Inno Setup, AppImage and DMG builds, and the built apps starting on Windows, macOS and Linux.
- Real Blender, winget, Homebrew, Flatpak and Snap installs; the winget IDs other than Blender and Godot were not looked up.
- Requests to the real OpenAI, Claude and Meta APIs, and the Inkling runner.
