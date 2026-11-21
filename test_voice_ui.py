"""Exercise the native Chat controls with a simulated microphone and local recognizer."""
import array
import tempfile
import threading
import time
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QBuffer, QIODevice, QObject, Signal, Qt
from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

import studio
import voice_dialog as voice


class VoiceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = root
        for key, path in [('HOME', root), ('STATE', root), ('SESSION', root/'studio.json'),
                          ('CONNECTIONS', root/'connections.json'), ('PROVIDERS', root/'providers.json')]:
            self.stack.enter_context(patch.object(studio, key, path))
        for method in ('health', 'refresh_mail_status', 'install_tray', '_poll_local_models'):
            self.stack.enter_context(patch.object(studio.Studio, method))
        self.stack.enter_context(patch.object(studio, 'EscapeCancel'))
        self.stack.enter_context(patch.object(voice, '_uses_keyboard_dictation', return_value=False))
        self.permissions = self.stack.enter_context(patch.object(
            voice, '_request_microphone', side_effect=lambda parent, resolved: resolved(True)))
        self.recognize = self.stack.enter_context(patch.object(voice, '_recognize'))
        self.w = studio.Studio()
        self.w.show()
        self.app.processEvents()
        def close():
            if isValid(self.w):
                self.w.allow_quit = True
                self.w.close()
                self.w.deleteLater()
                self.app.processEvents()
        self.stack.callback(close)

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def microphone(self, data):
        # A device supporting only its common 48 kHz stereo float format.
        native = QAudioFormat()
        native.setSampleFormat(QAudioFormat.SampleFormat.Float)
        native.setSampleRate(48000)
        native.setChannelCount(2)
        device = SimpleNamespace(isNull=lambda:False, preferredFormat=lambda:native,
                                 isFormatSupported=lambda fmt:fmt == native)
        class Source(QObject):
            stateChanged = Signal(object)
            def __init__(self, device, fmt, parent):
                super().__init__(parent)
                self.buffer = QBuffer(self)
                self.buffer.setData(data)
            def start(self):
                self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                return self.buffer
            def error(self):
                return SimpleNamespace(name='NoError')
            def stop(self):
                self.buffer.close()
        self.stack.enter_context(patch('PySide6.QtMultimedia.QMediaDevices.defaultAudioInput',return_value=device))
        return self.stack.enter_context(patch('PySide6.QtMultimedia.QAudioSource',side_effect=Source))

    def click(self, widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)

    def test_record_finish_transcribes_private_file_into_edited_draft(self):
        w = self.w
        self.assertIsNone(w.voice)
        self.permissions.assert_not_called()
        self.assertTrue(w.voice_record_button.isVisible())
        self.assertFalse(w.voice_read_button.isEnabled())
        source = self.microphone(array.array('f', [.25, -.25] * 2400).tobytes())
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        captured = []
        def recognize(path, state, stop, status):
            captured.append(path)
            with wave.open(str(path), 'rb') as audio:
                self.assertEqual((audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getnframes()),
                                 (48000, 2, 2, 2400))
                self.assertEqual(array.array('h', audio.readframes(1)).tolist(), [8191,-8191])
            self.assertEqual(state, self.root)
            status('Recognizing locally…')
            started.set()
            release.wait(4)
            return 'Spoken message.'
        self.recognize.side_effect = recognize
        w.prompt.setPlainText('Hello')
        self.click(w.voice_record_button)
        self.assertEqual(w.voice_record_button.text(), 'Finish')
        self.assertTrue(w.voice_cancel_button.isVisible())
        self.assertIn('Recording', w.voice_status.text())
        self.click(w.voice_record_button)
        self.wait(lambda:started.is_set() and w.voice_status.text()=='Recognizing locally…')
        self.assertEqual(w.voice_record_button.text(), 'Stop')
        w.prompt.setPlainText('Hello from my keyboard.')
        release.set()
        self.wait(lambda:w.voice.phase=='idle')
        self.assertEqual(w.prompt.toPlainText(),'Hello from my keyboard. Spoken message.')
        self.assertEqual(w.task['draft'], w.prompt.toPlainText())
        self.assertEqual(w.task['messages'], [])
        self.assertFalse(captured[0].exists())
        self.assertFalse(captured[0].parent.exists())
        source.assert_called_once()

    def test_permission_wait_cancel_denial_and_mac_keyboard_fallback(self):
        w = self.w
        waiting = []
        self.permissions.side_effect = lambda parent, result:waiting.append(result)
        with patch.object(voice, '_Microphone') as source:
            self.click(w.voice_record_button)
            self.assertEqual(w.voice.phase, 'permission')
            self.click(w.voice_record_button)
            waiting.pop()(True)
            self.assertEqual(w.voice.phase, 'idle')
            self.click(w.voice_record_button)
            waiting.pop()(False)
            self.assertIn('denied',w.voice_status.text())
            with patch.object(voice,'_uses_keyboard_dictation',return_value=True):
                self.click(w.voice_record_button)
            self.assertIn('Dictation shortcut', w.voice_status.text())
            self.assertTrue(w.prompt.hasFocus())
            source.assert_not_called()

    def test_empty_audio_empty_transcript_and_retry(self):
        w = self.w
        self.microphone(b'')
        self.click(w.voice_record_button)
        self.click(w.voice_record_button)
        self.wait(lambda:'No audio' in w.voice_status.text())
        self.recognize.assert_not_called()
        path = self.root / 'speech.wav'
        path.write_bytes(b'private user file')
        self.recognize.side_effect = ['   ', 'Second try']
        with patch.object(studio.QFileDialog, 'getOpenFileName', return_value=(str(path),'')):
            w.voice_file_action.trigger()
        self.wait(lambda:'No speech' in w.voice_status.text())
        w.transcribe_voice_file(path)
        self.wait(lambda:w.prompt.toPlainText()=='Second try')
        self.assertEqual(path.read_bytes(), b'private user file')

    def test_stop_then_retry_ignores_late_result_and_preserves_import(self):
        w = self.w
        path = self.root / 'keep.wav'
        path.write_bytes(b'keep')
        stop_events, started, release = [], threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def recognize(path, state, stop, status):
            stop_events.append(stop)
            if len(stop_events) == 1:
                started.set()
                release.wait(4)
                status('Obsolete')
                return 'Obsolete'
            return 'New transcript'
        self.recognize.side_effect = recognize
        w.transcribe_voice_file(path)
        self.wait(started.is_set)
        self.click(w.voice_record_button)
        self.assertTrue(stop_events[0].is_set())
        w.transcribe_voice_file(path)
        self.wait(lambda:w.prompt.toPlainText()=='New transcript')
        release.set()
        QTest.qWait(40)
        self.assertEqual(w.prompt.toPlainText(), 'New transcript')
        self.assertEqual(path.read_bytes(), b'keep')

    def test_recording_cancel_and_duration_cap(self):
        w = self.w
        self.microphone(array.array('f', [.2, -.2] * 48001).tobytes())
        self.click(w.voice_record_button)
        self.click(w.voice_cancel_button)
        self.recognize.assert_not_called()
        self.assertEqual(w.voice.phase, 'idle')
        def recognize(path, *args):
            with wave.open(str(path), 'rb') as audio:
                self.assertEqual(audio.getnframes(),48000)
            return 'Capped'
        self.recognize.side_effect = recognize
        with patch.object(voice,'MAX_RECORDING_SECONDS',1):
            self.click(w.voice_record_button)
            self.wait(lambda:w.prompt.toPlainText()=='Capped')

    def test_switching_chats_and_deleting_window_cancel_active_transcription(self):
        w = self.w
        stops = []
        returned = threading.Event()
        def recognize(path, state, stop, status):
            stops.append(stop)
            stop.wait(4)
            if len(stops)==2:
                returned.set()
            return 'Wrong chat'
        self.recognize.side_effect = recognize
        w.transcribe_voice_file(self.root/'one.wav')
        self.wait(lambda:len(stops)==1)
        w.new_task()
        self.assertTrue(stops[0].is_set())
        self.assertEqual(w.prompt.toPlainText(), '')
        w.switch_workspace('code')
        self.assertFalse(w.voice_record_button.isVisible())
        self.assertFalse(w.voice_read_button.isVisible())
        self.assertFalse(w.voice_file_action.isVisible())
        w.switch_workspace('chat')
        w.transcribe_voice_file(self.root/'two.wav')
        self.wait(lambda:len(stops)==2)
        delete(w)
        self.wait(returned.is_set)

    def test_read_aloud_uses_visible_latest_assistant_text_and_can_stop(self):
        w = self.w
        class Speaker(QObject):
            stateChanged = Signal(object)
            spoken = []
            def say(self,text):self.spoken.append(text)
            def stop(self):pass
        w.task['messages'] = [
            {'role':'assistant','content':'An earlier reply.'},
            {'role':'assistant','content':'The **latest** reply.'},
            {'role':'assistant','source':'app','content':'An app status.'},
            {'role':'assistant','source':'chat_partial','content':'Incomplete'},
            {'role':'assistant','source':'chat_error','content':'Interrupted'},
            {'role':'user','content':'Private user message'},
        ]
        w.render()
        self.assertTrue(w.voice_read_button.isEnabled())
        with patch.object(voice,'_create_speaker',side_effect=Speaker):
            self.click(w.voice_read_button)
            self.assertEqual(w.voice_read_button.text(), 'Stop reading')
            self.assertEqual(Speaker.spoken, ['The latest reply.'])
            self.click(w.voice_read_button)
            self.assertEqual(w.voice.phase,'idle')
            self.click(w.voice_read_button)
            w.voice.speaker.stateChanged.emit(SimpleNamespace(name='Ready'))
            self.assertEqual(w.voice_status.text(),'Finished reading.')
        w.voice.stop('')
        with patch.object(voice, '_create_speaker', side_effect=RuntimeError('Install a system voice.')):
            self.click(w.voice_read_button)
            self.assertEqual(w.voice.phase,'idle')
            self.assertIn('Install a system voice',w.voice_status.text())
        self.permissions.assert_not_called()
