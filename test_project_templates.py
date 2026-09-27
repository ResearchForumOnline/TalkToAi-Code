import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from project_templates import create_project_template, list_project_templates


class ProjectTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_metadata_is_detached_and_has_ui_fields(self):
        templates = list_project_templates()
        self.assertEqual({item['id'] for item in templates}, {'browser-game', 'python-cli'})
        for item in templates:
            self.assertTrue(item['title'])
            self.assertTrue(item['description'])
        templates[0]['title'] = 'changed'
        self.assertNotEqual(list_project_templates()[0]['title'], 'changed')

    def test_game_has_local_assets_and_runnable_entrypoint(self):
        result = create_project_template(self.root, 'browser-game', 'My Game')
        target = self.root.resolve() / 'My Game'
        self.assertEqual(result['status'], 'created')
        self.assertEqual(Path(result['project_path']), target)
        self.assertEqual(result['entrypoint'], 'index.html')
        self.assertIsInstance(result['launch_instructions'], str)
        self.assertEqual(set(result['files']), {'index.html', 'style.css', 'game.js', 'README.md'})
        html = (target / 'index.html').read_text(encoding='utf-8')
        self.assertIn('src="game.js"', html)
        self.assertIn('href="style.css"', html)
        self.assertNotIn('https://', html)
        self.assertNotIn('http://', html)
        self.assertFalse(list(self.root.glob('.talktoai-template-*')))

    def test_unknown_template_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            create_project_template(self.root, 'not-real', 'hello')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_traversal_absolute_names_reserved_names_rejected(self):
        for name in ('../escape', '..', '.', '/absolute', 'C:\\escape', 'folder/name',
                     'folder\\name', 'CON', 'nul', 'COM1', 'LPT9', 'a.', 'a ',
                     ' leading', '', 'a' * 65, 'name:stream', 'with\nnewline'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                create_project_template(self.root, 'python-cli', name)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_empty_directory_never_replaced(self):
        (self.root / 'Existing').mkdir()
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'browser-game', 'Existing')
        self.assertEqual(list((self.root / 'Existing').iterdir()), [])

    def test_existing_file_or_populated_directory_never_overwritten(self):
        (self.root / 'Existing').mkdir()
        retained = self.root / 'Existing' / 'index.html'
        retained.write_bytes(b'keep this exactly')
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'browser-game', 'Existing')
        self.assertEqual(retained.read_bytes(), b'keep this exactly')
        (self.root / 'File').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'python-cli', 'File')
        self.assertEqual((self.root / 'File').read_bytes(), b'keep')

    def test_linked_destination_and_workspace_rejected(self):
        actual = self.root / 'actual'
        actual.mkdir()
        link = self.root / 'linked'
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic links require OS permission.')
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'python-cli', 'linked')
        with self.assertRaises(ValueError):
            create_project_template(link, 'python-cli', 'new')
        self.assertEqual(list(actual.iterdir()), [])

    def test_broken_link_destination_rejected(self):
        link = self.root / 'broken'
        try:
            link.symlink_to(self.root / 'missing', target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic links require OS permission.')
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'python-cli', 'broken')
        self.assertTrue(link.is_symlink())

    def test_cancellation_before_and_after_staging_cleans_up(self):
        with self.assertRaises(InterruptedError):
            create_project_template(self.root, 'browser-game', 'Cancelled', cancelled=lambda: True)
        checks = 0
        def cancel_at_commit():
            nonlocal checks
            checks += 1
            return checks == 4  # initial check, two staged CLI files, commit check
        with self.assertRaises(InterruptedError):
            create_project_template(self.root, 'python-cli', 'Cancelled', cancelled=cancel_at_commit)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_commit_write_failure_removes_only_owned_files(self):
        original_open = Path.open
        def failing_open(path, mode='r', *args, **kwargs):
            if path.parent.name == 'Failure' and path.name == 'README.md' and mode == 'xb':
                raise OSError('simulated disk failure')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', failing_open), self.assertRaises(OSError):
            create_project_template(self.root, 'python-cli', 'Failure')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_destination_created_during_staging_is_not_replaced(self):
        checks = 0
        def race():
            nonlocal checks
            checks += 1
            if checks == 4:
                (self.root / 'Race').mkdir()
                (self.root / 'Race' / 'README.md').write_bytes(b'another process owns this')
            return False
        with self.assertRaises(FileExistsError):
            create_project_template(self.root, 'python-cli', 'Race', cancelled=race)
        self.assertEqual((self.root / 'Race' / 'README.md').read_bytes(), b'another process owns this')
        self.assertEqual(list(self.root.iterdir()), [self.root / 'Race'])

    def test_python_cli_round_trip_and_corrupt_data_preservation(self):
        result = create_project_template(self.root, 'python-cli', 'Taskbox')
        script = Path(result['project_path']) / result['entrypoint']
        data = self.root / 'tasks.json'
        def command(*args):
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            return subprocess.run([sys.executable, str(script), '--data', str(data), *args],
                                  capture_output=True, text=True, encoding='utf-8', timeout=15, env=env)
        self.assertEqual(command('add', 'Make a better game', '--priority', 'high').returncode, 0)
        self.assertEqual(command('add', 'Review notes').returncode, 0)
        self.assertEqual(command('done', '1').returncode, 0)
        self.assertNotIn('better game', command('list').stdout)
        self.assertIn('better game', command('list', '--all').stdout)
        self.assertEqual(command('undo', '1').returncode, 0)
        self.assertIn('better game', command('list', '--search', 'BETTER').stdout)
        self.assertEqual(command('remove', '2').returncode, 0)
        parsed = json.loads(data.read_text(encoding='utf-8'))
        self.assertEqual(len(parsed['tasks']), 1)
        self.assertEqual(parsed['next_id'], 3)
        self.assertIn('Open: 1 | Completed: 0 | Total: 1', command('stats').stdout)
        before = data.read_bytes()
        self.assertNotEqual(command('done', '99').returncode, 0)
        self.assertEqual(data.read_bytes(), before)
        data.write_bytes(b'{invalid data')
        failed = command('add', 'do not destroy data')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('not been changed', failed.stderr)
        self.assertEqual(data.read_bytes(), b'{invalid data')
        self.assertFalse(data.with_name('tasks.json.lock').exists())

    def test_python_cli_existing_lock_prevents_lost_writes(self):
        result = create_project_template(self.root, 'python-cli', 'Taskbox')
        script = Path(result['project_path']) / result['entrypoint']
        data = self.root / 'tasks.json'
        lock = self.root / 'tasks.json.lock'
        lock.write_text('another writer', encoding='utf-8')
        process = subprocess.run([sys.executable, str(script), '--data', str(data), 'add', 'blocked'],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 1)
        self.assertIn('in use', process.stderr)
        self.assertFalse(data.exists())
        self.assertEqual(lock.read_text(encoding='utf-8'), 'another writer')


@unittest.skipUnless(os.environ.get('TALKTOAI_TEMPLATE_BROWSER_TEST') == '1',
                     'Opt-in real browser smoke for generated local files')
class ProjectTemplateBrowserTests(unittest.TestCase):
    def test_generated_game_plays_pauses_restarts_and_makes_no_network_requests(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as work:
            result = create_project_template(work, 'browser-game', 'Smoke')
            entry = Path(result['project_path']) / result['entrypoint']
            with sync_playwright() as runtime:
                channel = os.environ.get('TALKTOAI_TEMPLATE_BROWSER_CHANNEL')
                if channel is None and os.name == 'nt':
                    channel = 'msedge'
                browser = runtime.chromium.launch(headless=True, **({'channel': channel} if channel else {}))
                try:
                    page = browser.new_page(viewport={'width': 1100, 'height': 900})
                    errors, network = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: network.append(request.url)
                            if request.url.startswith(('http:', 'https:')) else None)
                    page.goto(entry.as_uri())
                    self.assertEqual(page.title(), 'Neon Drift')
                    self.assertTrue(page.locator('#game').evaluate('(c) => c.width > 0 && c.height > 0'))
                    page.locator('#start').click()
                    page.wait_for_timeout(450)
                    self.assertTrue(page.locator('#overlay').is_hidden())
                    self.assertGreater(int(page.locator('#score').inner_text()), 0)
                    page.locator('#dash').click()
                    self.assertTrue(page.locator('#game').evaluate('(c) => document.activeElement === c'))
                    page.keyboard.press('p')
                    self.assertEqual(page.locator('#headline').inner_text(), 'Take a breath.')
                    score = page.locator('#score').inner_text()
                    page.wait_for_timeout(250)
                    self.assertEqual(page.locator('#score').inner_text(), score)
                    page.locator('#start').click()
                    page.keyboard.down('ArrowRight')
                    page.wait_for_timeout(300)
                    page.keyboard.up('ArrowRight')
                    self.assertGreater(int(page.locator('#score').inner_text()), int(score))
                    page.locator('#restart').click()
                    self.assertEqual(page.locator('#hull').inner_text(), '3 / 3')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    self.assertTrue(page.locator('#game').evaluate('(c) => c.clientWidth <= innerWidth'))
                    self.assertFalse(errors, errors)
                    self.assertFalse(network, network)
                finally:
                    browser.close()


if __name__ == '__main__':
    unittest.main()
