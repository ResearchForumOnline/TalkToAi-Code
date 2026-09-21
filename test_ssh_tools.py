import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from ssh_tools import SSHProfile, SSHSession, load_profiles, save_profiles, validate_alias


class SSHToolTests(unittest.TestCase):
    def test_alias_validation_and_metadata_roundtrip(self):
        self.assertEqual(validate_alias('amd-box'), 'amd-box')
        with self.assertRaises(ValueError):
            validate_alias('ssh -o ProxyCommand=bad')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'connections.json'
            save_profiles(path, [SSHProfile('AMD', 'amd-box', '~/games')])
            loaded = load_profiles(path)
            self.assertEqual(loaded[0].as_dict(), {'label': 'AMD', 'alias': 'amd-box', 'remote_path': '~/games'})

    def test_run_delegates_to_openssh_without_reading_keys(self):
        profile = SSHProfile('AMD', 'amd-box', '~/games')
        ok = type('Result', (), {'returncode': 0, 'stdout': 'hello', 'stderr': ''})()
        with patch('ssh_tools.subprocess.run', return_value=ok) as run:
            result = SSHSession(profile).run('pytest -q')
        self.assertIn('hello', result)
        args = run.call_args.args[0]
        self.assertEqual(args[0], 'ssh')
        self.assertIn('amd-box', args)
        self.assertNotIn('id_rsa', ' '.join(args))


if __name__ == '__main__':
    unittest.main()
