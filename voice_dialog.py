"""On-demand Chat dictation and read-aloud using the user's local devices."""
import array
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal, Slot


MAX_RECORDING_SECONDS = 90
MIC_HELP = 'Check microphone access in system settings and select a default input device. You can also use More → Transcribe WAV.'
SPEECH_HELP = 'Install a system speech voice and select a working audio output, then try Read aloud again.'

def _uses_keyboard_dictation():
    return sys.platform == 'darwin' and not getattr(sys, 'frozen', False)


def _request_microphone(parent, result):
    from PySide6.QtCore import QMicrophonePermission

    app = QCoreApplication.instance()
    permission = QMicrophonePermission()
    def resolved(*_):
        result(app.checkPermission(permission) == Qt.PermissionStatus.Granted)
    if app.checkPermission(permission) == Qt.PermissionStatus.Undetermined:
        app.requestPermission(permission, parent, resolved)
    else:
        resolved()


def _capture_format(device):
    from PySide6.QtMultimedia import QAudioFormat

    preferred = device.preferredFormat()
    for rate in dict.fromkeys([16000, preferred.sampleRate(), 48000, 44100]):
        if not 8000 <= rate <= 96000:
            continue
        for channels in (1, 2):
            for samples in dict.fromkeys([QAudioFormat.SampleFormat.Int16, preferred.sampleFormat()]):
                if samples.name not in {'Int16', 'Int32', 'UInt8', 'Float'}:
                    continue
                fmt = QAudioFormat()
                fmt.setSampleRate(rate)
                fmt.setChannelCount(channels)
                fmt.setSampleFormat(samples)
                if device.isFormatSupported(fmt):
                    return fmt
    raise RuntimeError('This microphone has no supported mono or stereo input format. ' + MIC_HELP)


class _Microphone(QObject):
    full = Signal()
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        try:
            from PySide6.QtMultimedia import QAudioSource, QMediaDevices
        except ImportError as exc:
            raise RuntimeError('The microphone driver for this app is unavailable. ' + MIC_HELP) from exc

        self.device = QMediaDevices.defaultAudioInput()
        if self.device.isNull():
            raise RuntimeError('No default microphone was found. ' + MIC_HELP)
        self.format = _capture_format(self.device)
        self.source = QAudioSource(self.device, self.format, self)
        self.input = None
        self.samples = bytearray()
        self.limit = self.format.bytesForFrames(self.format.sampleRate() * MAX_RECORDING_SECONDS)
        self.source.stateChanged.connect(self._state_changed)

    def start(self):
        self.input = self.source.start()
        if self.input is None or self.source.error().name != 'NoError':
            raise RuntimeError('The microphone could not start. ' + MIC_HELP)
        self.input.readyRead.connect(self._read)
        self._read()

    def _state_changed(self, state):
        if self.input is not None and state.name == 'StoppedState' and self.source.error().name != 'NoError':
            self.failed.emit('Microphone recording stopped unexpectedly. ' + MIC_HELP)

    def _drain(self):
        if self.input is None:
            return
        available = min(self.input.bytesAvailable(), self.limit - len(self.samples))
        if available > 0:
            self.samples.extend(bytes(self.input.read(available)))

    def _read(self):
        self._drain()
        if len(self.samples) >= self.limit:
            self.full.emit()

    def finish(self):
        self._drain()
        self.stop()
        return (self.samples, self.format.sampleRate(), self.format.channelCount(),
                self.format.sampleFormat().name)

    def stop(self):
        self.input = None
        self.source.stop()


def _write_capture(path, samples, rate, channels, sample_format):
    # Qt exposes native-endian PCM. Preserve the device rate/channels; the speech
    # backend resamples the WAV for both recorded and imported audio.
    width = {'UInt8': 1, 'Int16': 2, 'Int32': 4, 'Float': 4}[sample_format]
    usable = len(samples) // (width * channels) * (width * channels)
    if usable == 0:
        raise ValueError('No audio was recorded. Select Record and speak before finishing.')
    data = memoryview(samples)[:usable]
    if sample_format == 'Float':
        pcm = array.array('h', (int(max(-1., min(1., value)) * 32767) for value in data.cast('f')))
        if sys.byteorder == 'big':
            pcm.byteswap()
        data, width = pcm, 2
    elif sys.byteorder == 'big' and width > 1:
        pcm = array.array({2: 'h', 4: 'i'}[width])
        pcm.frombytes(data)
        pcm.byteswap()
        data = pcm
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(data)


def _recognize(path, state, stop, status):
    from voice_transcription import transcribe_audio
    return transcribe_audio(path, state, stop, status=status)


class _JobSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(int, str, str)


def _transcribe(signals, sequence, state, cancelled, path, capture):
    def status(message):
        if not cancelled.is_set():
            signals.progress.emit(sequence, message)
    result = error = ''
    try:
        if capture is not None:
            with tempfile.TemporaryDirectory(prefix='talktoai-dictation-') as private:
                path = Path(private) / 'recording.wav'
                _write_capture(path, *capture)
                if not cancelled.is_set():
                    result = _recognize(path, state, cancelled, status)
        elif not cancelled.is_set():
            result = _recognize(path, state, cancelled, status)
    except Exception as exc:
        error = str(exc) or 'Voice transcription failed. Please try again.'
    if not cancelled.is_set():
        signals.finished.emit(sequence, (result or '').strip(), error)


def _create_speaker(parent):
    try:
        from PySide6.QtTextToSpeech import QTextToSpeech
    except ImportError as exc:
        raise RuntimeError('A local text-to-speech engine is unavailable. ' + SPEECH_HELP) from exc

    installed = QTextToSpeech.availableEngines()
    for name in ('winrt', 'sapi', 'darwin', 'speechd', 'flite'):
        if name in installed:
            engine = QTextToSpeech(name, parent)
            if engine.state() != QTextToSpeech.State.Error:
                return engine
            engine.deleteLater()
    raise RuntimeError('A local text-to-speech engine is unavailable. ' + SPEECH_HELP)


class VoiceController(QObject):
    """Runs one voice action at a time; stale callbacks cannot modify a new draft."""
    changed = Signal(str, str)
    dictated = Signal(str)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state_dir = Path(state)
        self.phase = 'idle'
        self.sequence = 0
        self.microphone = self.speaker = self.cancelled = None
        self.started = 0.
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        # The worker owns this bridge until it exits, even when the window closes.
        self.signals = _JobSignals()
        self.signals.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.signals.finished.connect(self._transcribed, Qt.ConnectionType.QueuedConnection)

    def _set(self, phase, message):
        self.phase = phase
        self.changed.emit(phase, message)

    def record(self):
        if self.phase != 'idle':
            return
        if _uses_keyboard_dictation():
            self._set('idle', 'Use your Mac’s keyboard Dictation shortcut in the message box, or choose More → Transcribe WAV.')
            return
        self.sequence += 1
        sequence = self.sequence
        self._set('permission', 'Waiting for microphone permission…')
        try:
            _request_microphone(self, lambda granted: self._permitted(sequence, granted))
        except Exception as exc:
            self.stop('Microphone permission could not be requested. ' + MIC_HELP + ' ' + str(exc))

    def _permitted(self, sequence, granted):
        if sequence != self.sequence or self.phase != 'permission':
            return
        if not granted:
            self.stop('Microphone access was denied. ' + MIC_HELP)
            return
        try:
            self.microphone = _Microphone(self)
            self.microphone.failed.connect(self.stop)
            self.microphone.full.connect(self.finish)
            self.started = time.monotonic()
            self._set('recording', 'Recording · 0:00 / 1:30')
            self.microphone.start()
            if self.phase == 'recording':
                self.timer.start()
        except Exception as exc:
            self.stop(str(exc) or MIC_HELP)

    def _tick(self):
        elapsed = int(time.monotonic() - self.started)
        if elapsed >= MAX_RECORDING_SECONDS:
            self.finish()
        else:
            self._set('recording', f'Recording · {elapsed // 60}:{elapsed % 60:02d} / 1:30')

    def finish(self):
        if self.phase == 'recording':
            self.timer.stop()
            mic, self.microphone = self.microphone, None
            try:
                capture = mic.finish()
            except Exception as exc:
                self.stop(str(exc) or MIC_HELP)
            else:
                self._start_job(capture=capture)
            finally:
                mic.deleteLater()

    def transcribe_file(self, path):
        if self.phase == 'idle':
            self._start_job(path=Path(path))

    def _start_job(self, path=None, capture=None):
        self.sequence += 1
        self.cancelled = threading.Event()
        self._cancel_on_destroy = self.destroyed.connect(lambda *_, stop=self.cancelled: stop.set())
        self._set('transcribing', 'Transcribing on this computer…')
        worker = threading.Thread(target=_transcribe,
            args=(self.signals, self.sequence, self.state_dir, self.cancelled, path, capture), daemon=True)
        worker.start()

    @Slot(int, str)
    def _progress(self, sequence, message):
        if sequence == self.sequence and self.phase == 'transcribing':
            self._set('transcribing', message)

    @Slot(int, str, str)
    def _transcribed(self, sequence, result, error):
        if sequence != self.sequence or self.phase != 'transcribing':
            return
        self._end_job()
        if error or not result:
            self._set('idle', error or 'No speech was recognized. Try recording again.')
        else:
            self.dictated.emit(result)
            self._set('idle', 'Added to your draft. Review it and select Send.')

    def read(self, text):
        if self.phase != 'idle' or not text.strip():
            return
        try:
            if self.speaker is None:
                self.speaker = _create_speaker(self)
                self.speaker.stateChanged.connect(self._speech_state)
            self._set('speaking', 'Reading the latest reply aloud…')
            self.speaker.say(text)
        except Exception as exc:
            self.stop(str(exc) or SPEECH_HELP)

    def _speech_state(self, state):
        if self.phase == 'speaking' and self.sender() is self.speaker:
            if state.name == 'Ready':
                self._set('idle', 'Finished reading.')
            elif state.name == 'Error':
                self.stop('Read aloud could not play. ' + SPEECH_HELP)

    def stop(self, message='Stopped.'):
        self.sequence += 1
        self.phase = 'idle'
        self.timer.stop()
        self._end_job()
        if self.microphone is not None:
            self.microphone.stop()
            self.microphone.deleteLater()
            self.microphone = None
        if self.speaker is not None:
            self.speaker.stop()
            self.speaker.deleteLater()
            self.speaker = None
        self._set('idle', message)

    def _end_job(self):
        if self.cancelled is not None:
            self.cancelled.set()
            self.destroyed.disconnect(self._cancel_on_destroy)
            self.cancelled = None
