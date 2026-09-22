import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_core
from ssh_tools import discover_aliases

class DiscoveryTests(unittest.TestCase):
    def test_aliases_includes_and_cycles(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'config').write_text('Host website production\n IdentityFile private.key\nHost * !bad\nInclude more\n')
            (root/'more').write_text('Host game-box\nInclude config\n')
            self.assertEqual(discover_aliases(root/'config'),['website','production','game-box'])
    def test_connect_uses_existing_alias_only(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent_core,'ACTIVE_REMOTE_ALLOWED',True), patch.object(agent_core,'REMOTE_PILOT',True), patch('ssh_tools.discover_aliases',return_value=['website']), patch('ssh_tools.load_profiles',return_value=[]), patch('ssh_tools.SSHSession.test',return_value='connected'):
            tools=agent_core.ProjectTools(folder,act=True)
            result=tools.execute('connect_remote',{'alias':'website'})
            self.assertIn('true',result)
            self.assertEqual(tools.remote.alias,'website')
            with self.assertRaises(ValueError):tools.execute('connect_remote',{'alias':'unknown'})
    def test_plan_cannot_connect(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(PermissionError):agent_core.ProjectTools(folder,act=False).execute('connect_remote',{'alias':'website'})
