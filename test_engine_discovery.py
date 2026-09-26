import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine_discovery as engines


class EngineDiscoveryTests(unittest.TestCase):
    def test_configured_executable_wins_and_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'Godot.exe'; executable.write_bytes(b'fixture')
            with patch.dict(engines.os.environ, {'TALKTOAI_GODOT':str(executable)}):
                self.assertEqual(engines.find_godot(folder,folder),str(executable.resolve()))
            with patch.dict(engines.os.environ, {'TALKTOAI_GODOT':folder}):
                with self.assertRaisesRegex(ValueError,'Settings'):
                    engines.find_godot(folder,folder)

    def test_settings_and_godot4_path_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engines,'state_dir',return_value=Path(folder)), patch.dict(engines.os.environ,{'TALKTOAI_GODOT':''}):
            executable=Path(folder)/'Godot'; executable.write_bytes(b'fixture')
            import json
            (Path(folder)/'config.json').write_text(json.dumps({'godot_executable':str(executable)}))
            self.assertEqual(engines.configured_godot(),str(executable.resolve()))
            (Path(folder)/'config.json').write_text('{}')
            with patch.object(engines.shutil,'which',side_effect=lambda name:'/usr/bin/godot4' if name=='godot4' else None):
                self.assertEqual(engines.find_godot(folder,folder),'/usr/bin/godot4')
