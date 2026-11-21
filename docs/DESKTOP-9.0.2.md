# TalkToAi Code 9.0.2

## Voice in Chat

On Windows and supported Linux desktops, select **Record**, speak, then select **Finish**. TalkToAi puts the transcription in your draft. You can edit it, discard it, or send it. **Read aloud** plays the latest reply using a system voice. You can stop playback at any point.

- **Windows.** TalkToAi can use a Windows English (UK or US) desktop recognizer or an installed whisper.cpp runtime and model. Allow microphone access in Windows Privacy settings when prompted. If the desktop recognizer is missing, check the English language's speech options in Windows Settings or use Whisper. [Windows language features](https://support.microsoft.com/en-gb/windows/hardware/input-devices/manage-the-language-and-keyboard-input-layout-settings-in-windows)
- **macOS.** Put the cursor in the composer and use your Mac's keyboard Dictation to speak into it. To transcribe a recording locally in the app, use **Transcribe WAV** and an installed whisper.cpp runtime and model. **Read aloud** uses a macOS system voice.
- **Linux.** Microphone recording uses your default audio input. Transcription uses an installed whisper.cpp runtime and model. Reading aloud needs a Qt speech engine and an available local system voice, such as Speech Dispatcher with eSpeak NG.

TalkToAi opens the microphone when you choose to record. The built-in transcription paths process the audio on your computer and discard temporary recordings after the job. The transcript becomes an ordinary editable chat message. Once you send it, your selected chat model handles the text as it would any other message. Your Mac's system Dictation follows the settings in **System Settings → Keyboard**.

To use Whisper, install `whisper-cli`, then open **More → Open app data folder**. Create a `voice` folder and place a Whisper GGML Tiny or Base `.bin` model inside it. TalkToAi finds `whisper-cli` on your PATH or in `voice/bin`. To use specific files, set `TALKTOAI_WHISPER_CLI` and `TALKTOAI_WHISPER_MODEL` to their full local paths. Try **More → Transcribe WAV** to check the setup with a recording. [Whisper setup and models](https://github.com/ggml-org/whisper.cpp#quick-start) · [macOS Dictation](https://support.apple.com/en-gb/guide/mac-help/mh40584/mac) · [Qt system speech engines](https://doc.qt.io/qt-6/qttexttospeech-engines.html)

## Automatic tools, agents and local media

Ask Chat to open a browser, look something up, review a project, or generate media. Agent Zero chooses an available tool path from your request. A project with multiple independent questions can get up to three local reviewers. The Windows app can use supported desktop windows and selected Brave, Chrome or Edge sessions. Code keeps the project controls for longer work.

With ComfyUI running locally, try “Create an image of a lighthouse at blue hour.” The result appears in Chat. Video uses your installed ComfyUI video workflow and weights. Follow the [automatic tools and local media guide](DESKTOP-9.0.1.md#create-an-image-or-video).

Local Ollama models stay warm for an hour by default. **`/tune`** compares a cold and warm reply and can make a reversible retention change. Local model choices follow the confirmed Ollama inventory as you add or delete weights. See the [model guide and research references](DESKTOP-9.0.0.md).
