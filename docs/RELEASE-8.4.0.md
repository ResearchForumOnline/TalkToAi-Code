# TalkToAi Code 8.4.0 - document attachments and portable conversations

The 8.4.0 source checkpoint was not published as a separate GitHub release. These features are included in the 8.5.0 public release; see [8.5.0 verification](RELEASE-8.5.0.md) for current packaged checks and Store status.

This release adds an explicit **Attach files** control to the message composer, a bounded local text/PDF extraction path, and user-facing conversation copy/export. Existing project tools, model routing and technical task reports remain available.

## Attach files to a message

- Click **Attach files** or drop local files onto the composer. Selected files appear by name, extracted-text length and truncation status; remove one or clear all before sending.
- Supported inputs include UTF-8/UTF-16 text and code, Markdown, CSV/TSV, JSON/YAML, and PDFs with selectable text. The app reads file data locally and adds extracted text to the next user message as untrusted reference material.
- A message may include at most five files. Each file is limited to 2 MB and 12,000 extracted characters; the combined message limit is 20,000 extracted characters. Oversized, unreadable, unsupported, linked, or credential/key-named files receive an explicit error.
- Images and scanned PDFs without selectable text are not processed by this text route. No vision or OCR capability is claimed. Selecting a file does not by itself send it; pressing Send sends the extracted text to the selected model route. That route may be local, a configured server, or an optional API provider.
- After Send, extracted text is part of the local saved task history, so later turns can refer to it. The app does not make a separate binary artifact upload or copy the original file into the project. Selecting a sensitive-looking filename is blocked, but users should review ordinary files before attaching them.

## Conversation controls

**Conversation -> Copy conversation** copies the visible user/assistant transcript. **Export conversation** writes the same transcript as a UTF-8 Markdown file chosen by the user. The export uses visible attachment summaries rather than the extracted attachment body; tool observations, technical evidence and settings are outside this transcript. **Export task report** remains available for operational evidence.

**Reuse last prompt** places the most recent user request back in the composer for editing. It does not rerun a task or execute tools until the user sends it.

## Release verification

Verification evidence and download hashes will be added after source, packaged-app and portable workflow checks complete. Source changes alone do not prove an installed application has been upgraded.
