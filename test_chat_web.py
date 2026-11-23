"""Search works before generation, including on a model without function calling."""
import io
import json
import threading
import unittest
from unittest.mock import patch

from chat_web import lookup_chat_web, source_footer


class ChatWebTests(unittest.TestCase):
    def test_search_cards_keep_source_snippets_and_skip_breadcrumbs_and_nonpublic_links(self):
        links = [{'title': 'site.examplehttps://site.example', 'url': 'https://site.example/research'},
                 {'title': 'Research results', 'url': 'https://site.example/research'},
                 {'title': 'Duplicate', 'url': 'https://site.example/research'},
                 {'title': 'Local', 'url': 'http://127.0.0.1/secret'},
                 {'title': 'Nav', 'url': 'https://www.bing.com/search?q=fixture'}]
        with patch('chat_web.BrowserTools') as factory:
            browser = factory.return_value
            browser.execute.return_value = json.dumps({'search_engine':'bing', 'links':links})
            browser.search_snippets.return_value = {'Research results':'An observed result excerpt.'}
            result = lookup_chat_web('public search topic', threading.Event(), 'unused', browser_name='chrome')
            browser.execute.assert_called_once_with('search', 'public search topic', 'auto')
            browser.close.assert_called_once()
            self.assertEqual(factory.call_args.kwargs['mode'], 'headless')
        self.assertEqual(result['sources'], [{'title':'Research results', 'url':'https://site.example/research',
                                              'snippet':'An observed result excerpt.'}])
        self.assertIn('https://site.example/research', source_footer(result, 'Answer'))
        self.assertEqual(source_footer(result, 'See https://site.example/research'), '')

    def test_clean_machine_uses_bounded_key_free_search_without_a_browser(self):
        feed = b'<rss><channel><item><title>Test</title><link>https://example.org/a</link><description>Evidence</description></item></channel></rss>'
        with patch('chat_web.BrowserTools') as factory, patch('chat_web.urlopen', return_value=io.BytesIO(feed)) as get:
            factory.return_value.execute.side_effect = RuntimeError('Browser unavailable')
            result = lookup_chat_web('a public question', threading.Event(), 'unused')
        self.assertEqual(result['sources'][0]['snippet'], 'Evidence')
        self.assertEqual(get.call_args.args[0].full_url, 'https://www.bing.com/search?format=rss&q=a+public+question')

    def test_stop_and_explicit_engine_failure_do_not_start_a_new_search(self):
        with patch('chat_web.BrowserTools') as factory, patch('chat_web.urlopen') as get:
            browser = factory.return_value
            stop = threading.Event(); stop.set()
            with self.assertRaises(InterruptedError):
                lookup_chat_web('public question', stop, 'unused')
            browser.execute.assert_not_called()
            browser.execute.side_effect = RuntimeError('Browser unavailable')
            failed = lookup_chat_web('public question', threading.Event(), 'unused', search_engine='duckduckgo')
            self.assertFalse(failed['sources'])
            get.assert_not_called()
