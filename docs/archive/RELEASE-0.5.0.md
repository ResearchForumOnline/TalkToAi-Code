# TalkToAi Code 0.5.0

- Linux and macOS source installers now install host-appropriate dependencies, copy public runtime files only, and create user launchers. macOS also gets an Applications launcher.
- User state moved to the operating system's user data location on Linux and macOS; Windows keeps its existing app-data path.
- Project and user commands run through PowerShell on Windows or sh on Linux/macOS. The coding check runner uses the same platform command path.
- Remembered provider keys, mail OAuth tokens and ZeroThink sessions can use macOS Keychain or a Linux desktop keyring. Windows continues with its existing credential storage.
- Browser research, Chat/Code, local Ollama, SSH and project tools use portable paths. Windows accessibility PC Pilot and Windows startup shortcuts remain Windows-only.
- About & updates on Linux/macOS points users to source releases instead of offering a Windows installer.

Windows compilation, shell-script syntax, runtime source copy and packaged Windows app startup passed on this host. A clean Ubuntu runner and macOS runner each installed the source and opened the GUI in offscreen mode: https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36245686313 . Interactive use, platform keyring sign-in and inference remain unverified on those hosts.
