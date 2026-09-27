import tempfile
import unittest
from pathlib import Path

from task_navigation import canonical_source, desktop_projects, research_query, public_current_query, public_freshness_request


class TaskNavigationTests(unittest.TestCase):
    def test_generic_queries_do_not_leak_user_text(self):
        prompt = r'Make me a 2026 game in C:\Users\Alice\Desktop\SecretGame with password ABC123'
        query = research_query(prompt, 'Godot')
        self.assertIn('docs.godotengine.org', query)
        self.assertNotIn('SecretGame', query)
        self.assertNotIn('ABC123', query)
        self.assertNotIn('Alice', query)

    def test_offline_opt_out(self):
        self.assertIsNone(research_query('Make me a game offline', 'Godot'))
        self.assertIsNone(research_query('Improve app; do not search the web', 'Python'))

    def test_no_research_for_simple_local_edit(self):
        self.assertIsNone(research_query('Fix the typo in README.md', 'General'))

    def test_primary_source_fallback(self):
        self.assertEqual(canonical_source(research_query('Make me a game', 'Godot')),
                         'https://docs.godotengine.org/en/stable/tutorials/performance/optimizing_3d_performance.html')

    def test_public_current_chat_uses_fixed_official_query(self):
        query=public_current_query('What is the latest Godot release?')
        self.assertEqual(query,'site:godotengine.org Godot Engine latest release')
        self.assertEqual(canonical_source(query),'https://godotengine.org/download/archive/')
        self.assertTrue(public_freshness_request('What changed in AI research this week?'))

    def test_private_and_offline_chat_do_not_auto_search(self):
        self.assertIsNone(public_current_query('What is the latest Godot release for C:\\Users\\Alice\\secret project?'))
        self.assertIsNone(public_current_query('What is the latest Godot release? offline'))
        self.assertIsNone(public_current_query('What is the latest status of my server?'))

    def test_discovery_reads_manifests_not_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            game = home / 'Desktop' / 'NIGHTFALL'
            game.mkdir(parents=True)
            (game / 'project.godot').write_text('secret content', encoding='utf-8')
            private = home / 'Desktop' / 'secrets' / 'Hidden'
            private.mkdir(parents=True)
            (private / 'package.json').write_text('{}', encoding='utf-8')
            results = desktop_projects(home)
            self.assertEqual([item['kind'] for item in results['projects']], ['Godot game'])
            self.assertNotIn('secret content', str(results))
            self.assertNotIn('Hidden', str(results))


if __name__ == '__main__':
    unittest.main()
