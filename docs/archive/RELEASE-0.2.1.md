TalkToAi Code 0.2.1 adds **About & updates** to the sidebar.

- Check GitHub for a newer Windows release without using an AI model or API credits.
- Read release notes in the app, download the installer in the background, and verify its SHA-256 against GitHub asset metadata.
- Click Install update and quit when ready. Active agent work must finish or stop first. Updates are user initiated; nothing installs automatically.
- Start with Windows now launches into the tray when available.
- Saved settings now live alongside local task data, separate from installed program files.
- Desktop shortcut creation is selected by default in the Windows installer.

This remains a preview. The Windows installer is unsigned. Local model speed and coding quality depend on your model and hardware. Persistent task history is available; automatic cross-project semantic memory is not implemented.

Validation: update metadata/version tests, successful verified download, checksum mismatch cleanup, missing-digest and unexpected-download-location rejection, and existing desktop tests.
