TalkToAi Code 0.2.2 adds saved SSH connection discovery alongside the GitHub update panel introduced in 0.2.1.

- In Act mode, ask to connect to your server. The agent can inventory existing OpenSSH aliases (including Include files), select an existing alias, verify access and inspect a remote project before work.
- Explicit SSH connection requests make these tools available for that turn without requiring a previously selected Connections entry. Authentication uses your configured OpenSSH keys or agent.
- Saved app connection profiles remain supported. Unknown aliases are rejected. Password-only accounts and first-time host-key prompts still need interactive setup.
- About & updates checks GitHub, displays release notes and downloads SHA-256-verified installers without AI inference. Installation is initiated by the user after active work stops.
- Startup uses the tray flag, settings persist outside application binaries and Desktop shortcuts are selected by default.

Validation: 13 focused SSH, update and desktop tests passed. The actual SSH helper successfully connected to an existing configured server. The 0.2.1 installed runtime passed browser interaction, screenshot and vision-attachment checks; the 0.2.2 installer/runtime is checked again before publication.

Windows x64 preview. Unsigned installer. Local task history is supported; automatic cross-project semantic memory is not implemented. No model weights, keys, user profiles or server access are included.
