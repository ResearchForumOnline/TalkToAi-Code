import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from desktop_inventory import inspect_desktop
from providers import ProviderProfile, load_profiles, save_profiles
from desktop_tools import DesktopTools


class InventoryProviderTests(unittest.TestCase):
    def test_inventory_reports_metadata_without_reading_content(self):
        with patch('desktop_inventory.Path.home', return_value=Path(tempfile.mkdtemp())):
            result=inspect_desktop([])
        self.assertIn('private keys', result['note'])
        self.assertIn('ssh', result)

    def test_provider_profile_persists_only_key_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'providers.json'
            profile=ProviderProfile('Trial','https://example.invalid/v1','deepseek-chat','DEEPSEEK_API_KEY')
            save_profiles(path,[profile]);loaded=load_profiles(path)
            self.assertEqual(loaded[0].api_key_env,'DEEPSEEK_API_KEY')
            self.assertNotIn('secret', path.read_text(encoding='utf-8'))

    def test_desktop_tools_checkpoint_and_secret_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            tools=DesktopTools(act=True,state=Path(folder));tools.root=Path(folder).resolve()
            result=tools.execute('desktop_write_file',{'path':'notes.txt','content':'hello'})
            self.assertIn('Saved',result)
            self.assertEqual(tools.execute('desktop_read_file',{'path':'notes.txt'}),'hello')
            with self.assertRaises(PermissionError):tools.path('.ssh/config')


if __name__ == '__main__':unittest.main()
