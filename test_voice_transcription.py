"""Portable voice backend tests. Live Windows acceptance uses only prerecorded audio."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

import voice_transcription as voice


def write_wav(path, data, rate=16000, channels=1, width=2):
    with wave.open(str(path), 'wb') as output:
        output.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(data)


class VoiceTranscriptionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.wav = self.root / 'recording.wav'
        self.transcript = self.root / 'result.txt'
        self.cancel = threading.Event()
        write_wav(self.wav, struct.pack('<h', 900) * 160)

    def test_normalizes_stereo_pcm_to_16k_mono(self):
        write_wav(self.wav, struct.pack('<hhhhhh', 400, 600, 800, 1000, 1200, 1400),
                  rate=48000, channels=2)
        result = self.root / 'normalized.wav'
        voice.normalize_wav(self.wav, result, self.cancel)
        with wave.open(str(result), 'rb') as handle:
            self.assertEqual(handle.getparams()[:4], (1, 2, 16000, 1))
            self.assertEqual(struct.unpack('<h', handle.readframes(1)), (900,))
        write_wav(self.wav, bytes((128, 132)), rate=8000, width=1)
        voice.normalize_wav(self.wav, result, self.cancel)
        with wave.open(str(result), 'rb') as handle:
            self.assertEqual(struct.unpack('<hhhh', handle.readframes(4)), (0, 512, 1024, 1024))

    def test_rejects_truncated_and_over_duration_pcm(self):
        broken = self.wav.read_bytes()[:-2]
        self.wav.write_bytes(broken)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            voice.normalize_wav(self.wav, self.root / 'normalized.wav', self.cancel)
        write_wav(self.wav, b'\0\0' * 16160)
        with patch.object(voice, 'MAX_AUDIO_SECONDS', 1):
            with self.assertRaisesRegex(ValueError, 'up to 1 seconds'):
                voice.normalize_wav(self.wav, self.root / 'normalized.wav', self.cancel)

    def test_detects_app_local_executable_and_ggml_file(self):
        state = self.root / 'app state'
        directory = state / 'voice'
        binary = directory / 'bin' / ('whisper-cli.exe' if os.name == 'nt' else 'whisper-cli')
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixture')
        binary.chmod(0o700)
        model = directory / 'ggml-tiny.en-q5_1.bin'
        with model.open('wb') as data:
            data.write(b'lmgg')
            data.truncate(1024 ** 2)
        with patch.dict(os.environ, {'TALKTOAI_WHISPER_CLI': '', 'TALKTOAI_WHISPER_MODEL': ''}):
            self.assertEqual(voice.find_whisper(state), (binary, model))
        with patch.dict(os.environ, {'TALKTOAI_WHISPER_CLI': str(binary), 'TALKTOAI_WHISPER_MODEL': str(self.wav)}):
            with self.assertRaisesRegex(ValueError, 'Whisper GGML'):
                voice.find_whisper(state)

    def test_transcribe_uses_scoped_temp_files_and_keeps_source(self):
        temporary = []
        original = self.wav.read_bytes()
        notice = []
        binary = self.root / 'whisper-cli'
        weights = self.root / 'ggml-base.en.bin'

        def run(command, transcript, stop_event):
            capture = Path(command[command.index('-f') + 1])
            with wave.open(str(capture), 'rb') as handle:
                self.assertEqual(handle.getparams()[:3], (1, 2, 16000))
            self.assertEqual(command[command.index('-l') + 1], 'en')
            self.assertIn('-ng', command)
            self.assertEqual(stop_event, self.cancel)
            self.assertNotEqual(capture, self.wav)
            temporary.append(transcript.parent)
            transcript.write_bytes('  Generated\n   locally  '.encode('utf-8-sig'))
            return 0

        with patch('voice_transcription.find_whisper', return_value=(binary, weights)), \
             patch('voice_transcription._run_recognizer', side_effect=run):
            self.assertEqual(voice.transcribe_audio(self.wav, self.root, self.cancel, notice.append), 'Generated locally')
        self.assertEqual(self.wav.read_bytes(), original)
        self.assertTrue(not temporary[0].exists())
        self.assertTrue(any('local Whisper' in item for item in notice))

    def test_missing_windows_english_recognizer_reports_offline_setup(self):
        with patch('voice_transcription.find_whisper', return_value=None), \
             patch('voice_transcription._windows_powershell', return_value=self.root / 'powershell.exe'), \
             patch('voice_transcription._run_recognizer', return_value=42):
            with self.assertRaisesRegex(RuntimeError, r'English \(UK or US\) Windows desktop speech recognizer'):
                voice.transcribe_audio(self.wav, self.root, self.cancel)

    def test_cancelled_recording_does_not_find_or_launch_recognizer(self):
        self.cancel.set()
        with patch('voice_transcription.find_whisper') as find:
            with self.assertRaises(InterruptedError):
                voice.transcribe_audio(self.wav, self.root, self.cancel)
        find.assert_not_called()

    def test_real_child_writes_text_without_inheriting_provider_credentials(self):
        script = (
            'import os, sys; from pathlib import Path; '
            'assert "OPENAI_API_KEY" not in os.environ; '
            'sys.stdout.write("x" * 200000); sys.stderr.write("x" * 200000); '
            'Path(sys.argv[1]).write_text("Fixture transcript.", encoding="utf-8")'
        )
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-fixture-token'}):
            code = voice._run_recognizer([sys.executable, '-c', script, str(self.transcript)], self.transcript, self.cancel)
        self.assertEqual(code, 0)
        self.assertEqual(voice._read_transcript(self.transcript), 'Fixture transcript.')

    def test_real_child_is_stopped_after_recording_cancellation(self):
        signal = self.root / 'started'
        script = ('from pathlib import Path; import sys, time; '
                  'Path(sys.argv[1]).touch(); time.sleep(8); Path(sys.argv[2]).touch()')

        def wait_then_cancel():
            deadline = time.monotonic() + 4
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.cancel.set()

        stopper = threading.Thread(target=wait_then_cancel, daemon=True)
        stopper.start()
        try:
            with self.assertRaises(InterruptedError):
                voice._run_recognizer([sys.executable, '-c', script, str(signal), str(self.transcript)],
                                      self.transcript, self.cancel)
        finally:
            stopper.join(5)
        self.assertTrue(signal.is_file())
        self.assertFalse(self.transcript.exists())

    def test_real_child_timeout_and_invalid_transcript_are_bounded(self):
        with patch.object(voice, '_TIME_LIMIT', .1):
            with self.assertRaisesRegex(TimeoutError, 'shorter recording'):
                voice._run_recognizer([sys.executable, '-c', 'import time; time.sleep(8)'],
                                      self.transcript, self.cancel)
        self.transcript.write_bytes(b'x' * (voice._MAX_TRANSCRIPT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'too much'):
            voice._read_transcript(self.transcript)
        self.transcript.write_bytes(b' \n')
        with self.assertRaisesRegex(ValueError, 'No speech'):
            voice._read_transcript(self.transcript)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('TALKTOAI_NATIVE_VOICE_ACCEPTANCE') == '1',
                     'Live Windows prerecorded speech is opt-in')
class WindowsVoiceAcceptance(unittest.TestCase):
    def test_stock_offline_recognizer_transcribes_synthesized_wav(self):
        with tempfile.TemporaryDirectory(prefix='tac-voice-fixture-') as folder:
            root = Path(folder)
            wav = root / 'speech fixture.wav'
            env = dict(os.environ, TALKTOAI_VOICE_FIXTURE=str(wav))
            system = voice._windows_powershell()
            self.assertIsNotNone(system)
            script = """
            $ErrorActionPreference = 'Stop'
            Add-Type -AssemblyName System.Speech
            $speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
            try {
                $installed = $speaker.GetInstalledVoices() | Where-Object {
                    $_.Enabled -and $_.VoiceInfo.Culture.Name -in @('en-GB','en-US')
                } | Select-Object -First 1
                if (-not $installed) { throw 'A local English voice is required for the test fixture.' }
                $speaker.SelectVoice($installed.VoiceInfo.Name)
                $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
                    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
                    [System.Speech.AudioFormat.AudioChannel]::Mono)
                $speaker.SetOutputToWaveFile($env:TALKTOAI_VOICE_FIXTURE, $format)
                $speaker.Speak('The blue river flows past the old stone bridge. The clock is on the wooden table.')
            } finally { $speaker.Dispose() }
            """
            subprocess.run([str(system), '-NoProfile', '-NonInteractive', '-Command', script], check=True,
                           env=env, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            with patch('voice_transcription.find_whisper', return_value=None):
                transcript = voice.transcribe_audio(wav, root, threading.Event())
            self.assertIn('the blue river flows', transcript.lower())
            self.assertIn('the clock is on the wooden table', transcript.lower())


if __name__ == '__main__':
    unittest.main()
