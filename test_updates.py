import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from updates import select_release, download_release, version_tuple, REPO

class UpdatesTests(unittest.TestCase):
    def release(self):
        tag='v0.3.0-preview'; name='TalkToAi-Code-0.3.0-Windows-Setup.exe'
        return select_release(dict(tag_name=tag,assets=[dict(name=name,browser_download_url=f'{REPO}/releases/download/{tag}/{name}',digest='sha256:'+hashlib.sha256(b'test').hexdigest())]))
    def test_versions_and_assets(self):
        with patch('updates.VERSION','0.2.5'):self.assertTrue(self.release()['newer'])
        with patch('updates.VERSION','0.3.0'):self.assertFalse(self.release()['newer'])
        self.assertGreater(version_tuple('0.10.0'),version_tuple('0.9.0'))
        with self.assertRaises(ValueError):select_release(dict(tag_name='v0.3.0-preview',assets=[]))
    def test_verified_download(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen',return_value=io.BytesIO(b'test')):
            self.assertEqual(Path(download_release(self.release(),folder)).read_bytes(),b'test')
    def test_corruption_discards_file(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen',return_value=io.BytesIO(b'corrupt')):
            with self.assertRaises(ValueError):download_release(self.release(),folder)
            self.assertEqual(list(Path(folder).iterdir()),[])
    def test_missing_digest_and_wrong_host_rejected(self):
        release=self.release();release['digest']=None
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):download_release(release,folder)
            release=self.release();release['url']='https://example.com/program.exe'
            with self.assertRaises(ValueError):download_release(release,folder)
