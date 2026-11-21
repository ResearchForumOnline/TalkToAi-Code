"""Direct requests reach local tools without a manual Chat switch."""

import unittest

from chat_intent import chat_requests_tools, media_request, wants_browser_lookup, wants_screen_action


class ChatIntentTests(unittest.TestCase):
    def test_action_prompts_route_from_everyday_phrasing(self):
        for prompt in (
            'Hi, could you open Brave and show the Downloads page?',
            'Sir please check GitHub for the latest Godot release.',
            'Now click Save.',
            'Look up the current Plymouth weather.',
            'I need you to fix main.py',
            'Start Blender for me.',
            'Review the implementation, tests and README in this project.',
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(chat_requests_tools(prompt))
        self.assertTrue(wants_browser_lookup('Could you check this on GitHub?'))
        self.assertTrue(wants_screen_action('Please scroll down.'))

    def test_conversation_and_advice_have_no_extra_tool_turn(self):
        for prompt in (
            'Hello',
            'What does website accessibility mean?',
            'How can I close an app window?',
            'Could you show me how to use Brave?',
            'Find me some ideas for a project.',
            'Please give me examples of Blender scenes.',
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(chat_requests_tools(prompt))
        self.assertFalse(chat_requests_tools('Summarize this PDF about the Brave browser.', attached=True))
        self.assertTrue(chat_requests_tools('Read the attached document and then look up its data on the web.', attached=True))

    def test_concise_command_continues_the_last_tool_turn(self):
        self.assertTrue(chat_requests_tools('Now open the next one', followup=True))
        self.assertFalse(chat_requests_tools('Now open the next one'))
        self.assertFalse(chat_requests_tools('How do I open the next one?', followup=True))

    def test_media_requires_creation_intent(self):
        self.assertEqual(media_request('Please create an image of a lighthouse.'), ('image','create an image of a lighthouse.'))
        self.assertEqual(media_request('Hi, make me a short video of a rainy street.'), ('video','make me a short video of a rainy street.'))
        self.assertEqual(media_request('Make a 5-second video of a rainy street.')[0], 'video')
        self.assertEqual(media_request('Draw a glass house.'), ('image','a glass house.'))
        for prompt in ('How can I create a video?', 'Write a prompt for a photo.', 'Draw up a plan.',
                       'Draw my conclusion.', 'Create an icon in SVG for my game.', 'Create an image'):
            with self.subTest(prompt=prompt):
                self.assertIsNone(media_request(prompt))


if __name__ == '__main__':
    unittest.main()
