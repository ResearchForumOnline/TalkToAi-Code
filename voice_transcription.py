"""Offline transcription using an installed whisper.cpp CLI or Windows System.Speech."""
from __future__ import annotations

from array import array
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave


WHISPER_SETUP_URL = 'https://github.com/ggml-org/whisper.cpp#quick-start'
WHISPER_MODELS_URL = 'https://huggingface.co/ggerganov/whisper.cpp/tree/main'
MAX_AUDIO_SECONDS = 90
SAMPLE_RATE = 16000
_MAX_WAV_BYTES = MAX_AUDIO_SECONDS * 96000 * 2 * 4 + 1024 ** 2
_MAX_TRANSCRIPT_BYTES = 65536
_TIME_LIMIT = 180
_MISSING_WINDOWS_RECOGNIZER = 42
_MODEL_NAMES = (
    'ggml-tiny.en-q5_1.bin', 'ggml-tiny.en-q8_0.bin', 'ggml-tiny.en.bin',
    'ggml-tiny-q5_1.bin', 'ggml-tiny-q8_0.bin', 'ggml-tiny.bin',
    'ggml-base.en-q5_1.bin', 'ggml-base.en-q8_0.bin', 'ggml-base.en.bin',
    'ggml-base-q5_1.bin', 'ggml-base-q8_0.bin', 'ggml-base.bin',
)
_PROCESS_ENV = {
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'USERPROFILE',
    'HOME', 'USERNAME', 'USER', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES',
    'PROGRAMFILES(X86)', 'PROGRAMDATA', 'COMMONPROGRAMFILES', 'TEMP', 'TMP',
    'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH',
}
_WINDOWS_COMMAND = r"""
$ErrorActionPreference = 'Stop'
$engine = $null
try {
    Add-Type -AssemblyName System.Speech
    $recognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    $culture = [System.Globalization.CultureInfo]::CurrentUICulture.Name
    $preferred = if ($culture -eq 'en-GB') { 'en-GB','en-US' } else { 'en-US','en-GB' }
    foreach ($name in $preferred) {
        foreach ($info in $recognizers) {
            if ($info.Culture.Name -ne $name) { continue }
            try {
                $engine = [System.Speech.Recognition.SpeechRecognitionEngine]::new($info)
                $engine.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
                break
            } catch {
                if ($engine) { $engine.Dispose() }
                $engine = $null
            }
        }
        if ($engine) { break }
    }
    if (-not $engine) { exit 42 }
} catch {
    if ($engine) { $engine.Dispose() }
    exit 42
}
try {
    $engine.SetInputToWaveFile($env:TALKTOAI_VOICE_WAV)
    $parts = [System.Collections.Generic.List[string]]::new()
    # The desktop engine clears AudioFormat at EOF. Recognize() called
    # again after that transition throws instead of returning a null match.
    while ($parts.Count -lt 200 -and $null -ne $engine.AudioFormat) {
        $part = $engine.Recognize()
        if ($null -eq $part) { break }
        if ($part.Text) { $parts.Add($part.Text) }
    }
    [System.IO.File]::WriteAllText(
        $env:TALKTOAI_VOICE_TRANSCRIPT, ($parts -join ' '), [System.Text.UTF8Encoding]::new($false)
    )
} finally {
    $engine.Dispose()
}
"""


def _check_stop(stop_event):
    if stop_event.is_set():
        raise InterruptedError('Voice transcription stopped.')


def _wave_data(path):
    if not path.is_file():
        raise FileNotFoundError('The voice recording could not be found.')
    if path.stat().st_size > _MAX_WAV_BYTES:
        raise ValueError('The WAV recording is too large for local transcription.')
    try:
        with wave.open(str(path), 'rb') as handle:
            channels, width, rate, frames, compression, _ = handle.getparams()
            if compression != 'NONE' or channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 96000:
                raise ValueError('Use an uncompressed mono or stereo PCM WAV recording at 8–96 kHz.')
            if not frames:
                raise ValueError('The recording is empty. Record a short speech sample and try again.')
            if frames > MAX_AUDIO_SECONDS * rate:
                raise ValueError(f'Voice transcription accepts recordings up to {MAX_AUDIO_SECONDS} seconds.')
            data = handle.readframes(frames)
            if len(data) != frames * channels * width:
                raise ValueError('The WAV recording is incomplete. Record it again before transcribing.')
    except (wave.Error, EOFError):
        raise ValueError('The recording is not a supported PCM WAV file.') from None
    return data, channels, width, rate


def _mono_samples(data, channels, width):
    if width == 1:
        values = ((sample - 128) << 8 for sample in data)
    elif width == 3:
        offset = 1 if sys.byteorder == 'little' else 0
        values = (int.from_bytes(data[index:index + 2], sys.byteorder, signed=True)
                  for index in range(offset, len(data), 3))
    else:
        samples = array('h' if width == 2 else 'i')
        samples.frombytes(data)
        if channels == 1 and width == 2:
            return samples
        values = iter(samples) if width == 2 else (sample >> 16 for sample in samples)
    if channels == 2:
        return array('h', ((left + right) // 2 for left, right in zip(values, values)))
    return array('h', values)


def _resample(samples, rate, stop_event):
    if rate == SAMPLE_RATE:
        return samples
    result = array('h')
    count = max(1, len(samples) * SAMPLE_RATE // rate)
    input_end = len(samples) * SAMPLE_RATE
    for index in range(count):
        if index % 4096 == 0:
            _check_stop(stop_event)
        left = index * rate
        first, fraction = divmod(left, SAMPLE_RATE)
        if rate < SAMPLE_RATE:
            following = samples[min(first + 1, len(samples) - 1)]
            result.append((samples[first] * (SAMPLE_RATE - fraction) + following * fraction) // SAMPLE_RATE)
            continue
        right = min(left + rate, input_end)
        last = (right - 1) // SAMPLE_RATE
        total = sum(
            samples[part] * (min(right, (part + 1) * SAMPLE_RATE) - max(left, part * SAMPLE_RATE))
            for part in range(first, last + 1)
        )
        result.append(total // (right - left))
    return result


def normalize_wav(wav_path, output_path, stop_event):
    """Write a verified 16 kHz mono PCM16 WAV from an ordinary integer PCM WAV."""
    _check_stop(stop_event)
    data, channels, width, rate = _wave_data(Path(wav_path))
    if (channels, width, rate) != (1, 2, SAMPLE_RATE):
        samples = _resample(_mono_samples(data, channels, width), rate, stop_event)
        data = samples.tobytes()
    _check_stop(stop_event)
    with wave.open(str(output_path), 'wb') as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        output.writeframes(data)


def _explicit_path(variable):
    setting = os.environ.get(variable, '').strip()
    if not setting:
        return None
    path = Path(setting).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f'{variable} must point to an existing local file by its full path.')
    return path.resolve()


def _executable(path):
    return path.is_file() and (path.suffix.lower() == '.exe' if os.name == 'nt' else os.access(path, os.X_OK))


def _model_file(path):
    if path.suffix.lower() != '.bin' or not path.is_file():
        return False
    try:
        if not 1024 ** 2 <= path.stat().st_size <= 1024 ** 3:
            return False
        with path.open('rb') as handle:
            return handle.read(4) == b'lmgg'
    except OSError:
        return False


def find_whisper(state):
    """Return a verified local executable path and a GGML model path when available."""
    voice = Path(state) / 'voice'
    binary = _explicit_path('TALKTOAI_WHISPER_CLI')
    model = _explicit_path('TALKTOAI_WHISPER_MODEL')
    if binary and not _executable(binary):
        raise ValueError('TALKTOAI_WHISPER_CLI must point to the native whisper-cli executable.')
    if model and not _model_file(model):
        raise ValueError('TALKTOAI_WHISPER_MODEL must point to a Whisper GGML .bin model.')
    if not binary:
        executable = 'whisper-cli.exe' if os.name == 'nt' else 'whisper-cli'
        candidates = [voice / 'bin' / executable, voice / executable]
        cli = shutil.which(executable)
        if cli:
            candidates.append(Path(cli))
        if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):
            candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Microsoft/WinGet/Links' / executable)
        if os.name != 'nt':
            candidates.extend([Path('/opt/homebrew/bin') / executable, Path('/usr/local/bin') / executable,
                               Path.home() / '.local/bin' / executable])
        binary = next((path.resolve() for path in candidates if _executable(path)), None)
    if not model:
        directories = (voice, voice / 'models', Path.home() / '.cache/whisper.cpp/models')
        model = next((path.resolve() for folder in directories
                      for name in _MODEL_NAMES if _model_file(path := folder / name)), None)
    return (binary, model) if binary and model else None


def _windows_powershell():
    if os.name != 'nt':
        return None
    system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    command = system_root / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return command if command.is_file() else None


def _run_recognizer(command, transcript, stop_event, *, extra_env=None):
    _check_stop(stop_event)
    environment = {key: value for key, value in os.environ.items() if key.upper() in _PROCESS_ENV}
    if extra_env:
        environment.update(extra_env)
    process = subprocess.Popen(
        command, cwd=transcript.parent, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    deadline = time.monotonic() + _TIME_LIMIT
    try:
        while process.poll() is None:
            _check_stop(stop_event)
            if time.monotonic() >= deadline:
                raise TimeoutError('Local speech recognition took too long. Try a shorter recording.')
            try:
                if transcript.stat().st_size > _MAX_TRANSCRIPT_BYTES:
                    raise ValueError('The recognizer produced too much transcript output.')
            except FileNotFoundError:
                pass
            stop_event.wait(.05)
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise RuntimeError('The local recognizer did not exit after Stop.') from None
    _check_stop(stop_event)
    return process.returncode


def _read_transcript(path):
    try:
        with path.open('rb') as handle:
            data = handle.read(_MAX_TRANSCRIPT_BYTES + 1)
    except FileNotFoundError:
        raise RuntimeError('The local recognizer did not write a transcript. Check its installation.') from None
    if len(data) > _MAX_TRANSCRIPT_BYTES:
        raise ValueError('The recognizer produced too much transcript output.')
    try:
        text = ' '.join(data.decode('utf-8-sig').split())
    except UnicodeDecodeError:
        raise ValueError('The local recognizer returned unreadable text.') from None
    if not text:
        raise ValueError('No speech was recognized. Record again and speak clearly near the microphone.')
    return text


def _setup_message(state, windows=False):
    path = Path(state) / 'voice'
    windows_hint = 'Install an English (UK or US) Windows desktop speech recognizer, or ' if windows else ''
    return (f'{windows_hint}install whisper.cpp and put a GGML Tiny or Base model in {path}. '
            f'Whisper setup: {WHISPER_SETUP_URL}')


def transcribe_audio(wav_path, state, stop_event, status=lambda _message: None):
    """Transcribe a private, prerecorded PCM WAV and return text for review."""
    _check_stop(stop_event)
    whisper = find_whisper(state)
    system_speech = _windows_powershell() if whisper is None else None
    if whisper is None and system_speech is None:
        raise RuntimeError(_setup_message(state, os.name == 'nt'))
    with tempfile.TemporaryDirectory(prefix='talktoai-voice-', ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        normalized, transcript = root / 'input.wav', root / 'transcript.txt'
        normalize_wav(wav_path, normalized, stop_event)
        if whisper:
            binary, model = whisper
            language = 'en' if re.search(r'\.en(?:-|\.bin\Z)', model.name, re.I) else 'auto'
            status('Transcribing with local Whisper…')
            try:
                code = _run_recognizer([
                    str(binary), '-m', str(model), '-f', str(normalized), '-otxt', '-of',
                    str(transcript.with_suffix('')), '-l', language, '-t', str(min(6, os.cpu_count() or 1)),
                    '-ng', '-np', '-nt',
                ], transcript, stop_event)
            except OSError as exc:
                if isinstance(exc, (InterruptedError, TimeoutError)):
                    raise
                raise RuntimeError('Could not start the local whisper-cli. Keep its required libraries with the executable.') from None
            if code:
                raise RuntimeError(f'Local Whisper exited with code {code}. Check the model file and CLI installation.')
        else:
            status('Transcribing with Windows offline English speech recognition…')
            code = _run_recognizer([
                str(system_speech), '-NoProfile', '-NoLogo', '-NonInteractive', '-Command', _WINDOWS_COMMAND,
            ], transcript, stop_event, extra_env={
                'TALKTOAI_VOICE_WAV': str(normalized), 'TALKTOAI_VOICE_TRANSCRIPT': str(transcript),
            })
            if code == _MISSING_WINDOWS_RECOGNIZER:
                raise RuntimeError(_setup_message(state, windows=True))
            if code:
                raise RuntimeError('The Windows offline speech engine could not read this recording. Try recording again or use Whisper.')
        _check_stop(stop_event)
        return _read_transcript(transcript)
