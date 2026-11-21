TalkToAi Code 0.2.3 adds editable project memory and everyday UI improvements.

- **Project memory** in the sidebar, or Ctrl+Shift+M: maintain project decisions, conventions, test commands and next steps. Notes live in `.talktoai-code/PROJECT_MEMORY.md` and are included in subsequent tasks for that project. Conflicting edits are detected before saving. Notes are capped at 12,000 characters to limit context cost.
- Draft autosave after a short typing pause, retaining unsent text across restarts.
- Ctrl+, opens Settings. Ctrl+K now also offers Project memory, About & updates, Open app data folder and Open project folder.
- Includes the GitHub update panel and saved SSH alias discovery from 0.2.1–0.2.2.

Validation: 34 tests covering project memory persistence/isolation/conflicting edits, update checks/download verification, SSH discovery, agent behavior and desktop controls. Packaged installation and runtime smoke checks are completed before publication.

Memory notes are included in requests to your selected model, including remote/API models; never put passwords or keys in them. This is editable project context, not automatic semantic recall. Windows x64 preview; installer unsigned. Local inference speed and quality depend on hardware/model. Model weights and third-party game engines are separate downloads.
