import tempfile
import unittest
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zero_avatar import ASSETS, CLIPS, ZeroAvatar


class ZeroAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def widget(self, **options):
        avatar = ZeroAvatar(**options)
        self.addCleanup(avatar.close)
        avatar.show()
        self.app.processEvents()
        return avatar

    def test_assets_decode_and_visible_frames_change(self):
        avatar = self.widget()
        portrait = avatar.portrait
        for state in CLIPS:
            avatar.display(state, state)
            self.assertFalse(portrait.sheet.isNull(), state)
        avatar.display('working', 'Waiting for the model')
        start = portrait.grab().toImage()
        QTest.qWait(220)
        self.assertGreater(portrait.frame, 0)
        self.assertNotEqual(start, portrait.grab().toImage())
        avatar.hide()
        self.assertFalse(portrait.timer.isActive())

    def test_events_and_completion_follow_real_work(self):
        avatar = self.widget()
        avatar.begin('conversation')
        self.assertEqual(avatar.state, 'waking')
        avatar.next_clip()
        self.assertEqual(avatar.state, 'connecting')
        avatar.event_received('route', {})
        avatar.event_received('delta', 'First streamed token')
        self.assertEqual(avatar.status.text(), 'Writing a reply')
        avatar.event_received('tool', {'name': 'read_file', 'args': {'path': 'scene.py'}})
        self.assertEqual(avatar.state, 'waiting')
        avatar.event_received('tool', {'name': 'write_file', 'args': {'path': 'scene.py'}})
        self.assertEqual(avatar.state, 'making')
        avatar.progress(65, 2)
        self.assertIn('Step 2', avatar.status.text())
        self.assertIn('1m 05s', avatar.status.text())
        avatar.finish('Task complete')
        self.assertEqual(avatar.state, 'complete')
        avatar.next_clip()
        self.assertEqual(avatar.state, 'idle')
        avatar.begin('model')
        avatar.event_received('error', 'A provider error')
        avatar.finish('Task complete')
        self.assertEqual(avatar.state, 'idle')
        self.assertEqual(avatar.status.text(), 'This run needs attention')

    def test_motion_control_and_missing_clip_use_still(self):
        avatar = self.widget(motion=False)
        flags = []
        avatar.motion_changed.connect(flags.append)
        self.assertFalse(avatar.portrait.timer.isActive())
        self.assertFalse(avatar.portrait.still.isNull())
        avatar.motion_button.click()
        self.assertEqual(flags, [True])
        self.assertTrue(avatar.portrait.timer.isActive())
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder)
            (assets / 'still.jpg').write_bytes((ASSETS / 'still.jpg').read_bytes())
            fallback = self.widget(assets=assets)
            fallback.begin('model')
            self.assertEqual(fallback.state, 'connecting')
            self.assertFalse(fallback.portrait.still.isNull())
            self.assertFalse(fallback.portrait.timer.isActive())


if __name__ == '__main__':
    unittest.main()
