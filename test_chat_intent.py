"""Direct requests reach local tools without a manual Chat switch."""

import unittest

from chat_intent import chat_requests_tools, chat_web_query, media_request, requests_project_creation, wants_browser_lookup, wants_screen_action


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
        for text in ('Try again', 'Carry on.', 'hmm oh dear seems you cannot do anything you have full access to this computer do whatever you need etc install come on sir try harder'):
            self.assertTrue(chat_requests_tools(text, followup=True))
            self.assertFalse(chat_requests_tools(text))

    def test_media_requires_creation_intent(self):
        self.assertEqual(media_request('Please create an image of a lighthouse.'), ('image','create an image of a lighthouse.'))
        self.assertEqual(media_request('Hi, make me a short video of a rainy street.'), ('video','make me a short video of a rainy street.'))
        self.assertEqual(media_request('Make a 5-second video of a rainy street.')[0], 'video')
        self.assertEqual(media_request('Draw a glass house.'), ('image','a glass house.'))
        for prompt in ('How can I create a video?', 'Write a prompt for a photo.', 'Draw up a plan.',
                       'Draw my conclusion.', 'Create an icon in SVG for my game.', 'Create an image'):
            with self.subTest(prompt=prompt):
                self.assertIsNone(media_request(prompt))

    def test_creative_requests_go_to_project_tools_but_text_and_media_keep_their_routes(self):
        for text in ('create soemthing of your choosing to impress me', 'Create something impressive.',
                     'Please make me a little HTML art project.', 'Build a playable game in the browser.',
                     'Create a video game about a frog.', 'Create a small game.'):
            with self.subTest(text=text):
                self.assertTrue(requests_project_creation(text))
                self.assertTrue(chat_requests_tools(text))
        for text in ('How do I create a webpage?', 'Create a poem about HTML', 'Write a story about a browser game.',
                     'Create a video of an interactive game', 'Create an agent named Atlas for HTML',
                     'Make me an HTML project outline', 'Create a script but do not write files.'):
            with self.subTest(text=text):
                self.assertFalse(requests_project_creation(text))
        self.assertIsNone(media_request('Create a video game about a frog.'))

    def test_public_web_query_respects_private_context_and_handles_current_questions(self):
        self.assertEqual(chat_web_query('What is the latest Godot release?'), 'site:godotengine.org Godot Engine latest release')
        self.assertEqual(chat_web_query('Please search the web for lunar eclipses.'), 'lunar eclipses')
        self.assertIsNotNone(chat_web_query('What changed in AI research this week?'))
        self.assertIsNone(chat_web_query('What is the latest stable version?', document_history=True))
        self.assertEqual(chat_web_query('Search the web for lunar eclipses', document_history=True), 'lunar eclipses')
        self.assertIsNone(chat_web_query('Search the web for eclipses', attached=True))
        self.assertFalse(chat_requests_tools('Search the web for Python releases but do not use the internet.'))
        for text in ('Today I want to write a story.', 'What is my current bill?', 'What is the latest release? Offline.',
                     'Look up our confidential repository', 'What is the latest version installed on my PC?',
                     'Search online for C:\\Users\\Alex\\project', 'Search the web for jellyfish then save it to a file'):
            with self.subTest(text=text):
                self.assertIsNone(chat_web_query(text))



class TrySearchTests(unittest.TestCase):
    """"try search the web for nvidia info" once fell through to a reply that said it had no web access."""

    def test_try_before_an_action_is_a_request_for_the_action(self):
        from chat_intent import chat_requests_tools, chat_web_query, request_body, requests_tool_continuation
        for text in ('try search the web for nvidia info', 'try to search the web for nvidia info',
                     'can you try search the web for nvidia info', 'try searching the web for nvidia info'):
            with self.subTest(text=text):
                self.assertTrue(chat_requests_tools(text))
                self.assertEqual(chat_web_query(text), 'nvidia info')
        self.assertEqual(request_body('please try and look up nvidia news'), 'look up nvidia news')
        self.assertEqual(chat_web_query('google nvidia info'), 'nvidia info')
        self.assertEqual(chat_web_query('look online for nvidia earnings'), 'nvidia earnings')
        for text in ('try again', 'try harder', 'sir try again'):
            with self.subTest(text=text):
                self.assertTrue(requests_tool_continuation(text))
                self.assertFalse(chat_requests_tools(text))
        for text in ('try it', 'try this recipe idea', 'try to explain recursion', 'google sheets formula help',
                     'how do i google something'):
            with self.subTest(text=text):
                self.assertFalse(chat_requests_tools(text))
                self.assertIsNone(chat_web_query(text))


if __name__ == '__main__':
    unittest.main()
