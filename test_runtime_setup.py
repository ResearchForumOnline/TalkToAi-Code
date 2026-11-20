import threading
import unittest
from unittest.mock import Mock, patch

import runtime_setup as setup
from model_library import DownloadCancelled


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.cancel = threading.Event()
        self.notify = Mock()

    def test_ready_service_never_triggers_install(self):
        with patch.object(setup.routing, 'require_local_runtime', return_value=True), patch.object(setup, '_install') as install:
            self.assertTrue(setup.require_model_runtime(self.cancel, self.notify))
            install.assert_not_called()

    def test_missing_runtime_installs_then_rechecks(self):
        with patch.object(setup.sys, 'platform', 'win32'), patch.object(setup.routing, 'ollama_executable', return_value=None), \
             patch.object(setup.routing, 'require_local_runtime', side_effect=[setup.routing.OllamaMissingError(), True]) as check, \
             patch.object(setup, '_install') as install:
            self.assertTrue(setup.require_model_runtime(self.cancel, self.notify))
        self.assertEqual(check.call_count, 2)
        install.assert_called_once_with(self.cancel, self.notify)

    def test_broken_existing_install_reports_original_problem(self):
        with patch.object(setup.sys, 'platform', 'win32'), patch.object(setup.routing, 'ollama_executable', return_value='ollama.exe'), \
             patch.object(setup.routing, 'require_local_runtime', side_effect=ConnectionError('port in use')), patch.object(setup, '_install') as install:
            with self.assertRaisesRegex(ConnectionError, 'port in use'):
                setup.require_model_runtime(self.cancel, self.notify)
            install.assert_not_called()

    def test_absent_package_manager_reports_official_fallback(self):
        with patch.dict(setup.os.environ, {'LOCALAPPDATA': ''}):
            with self.assertRaisesRegex(ConnectionError, 'Get Ollama'):
                setup._install(self.cancel, self.notify)

    def test_package_manager_failure_reports_hex_code(self):
        process = Mock(returncode=-1)
        process.poll.return_value = -1
        with patch.object(setup, '_winget', return_value='winget.exe'), patch.object(setup.subprocess, 'Popen', return_value=process) as run, \
             patch.object(setup.routing, 'ollama_executable', return_value=None):
            with self.assertRaisesRegex(ConnectionError, '0xFFFFFFFF'):
                setup._install(self.cancel, self.notify)
        self.assertIn('Ollama.Ollama', run.call_args.args[0])
        self.assertIn('--scope', run.call_args.args[0])

    def test_pre_cancelled_install_never_launches(self):
        self.cancel.set()
        with patch.object(setup, '_install') as install:
            with self.assertRaises(DownloadCancelled):
                setup.require_model_runtime(self.cancel, self.notify)
            install.assert_not_called()
