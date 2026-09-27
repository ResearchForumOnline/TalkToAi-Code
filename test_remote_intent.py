import unittest

from remote_intent import requests_remote_work


class RemoteIntentTests(unittest.TestCase):
    def test_explicit_remote_machine_work(self):
        requests = (
            'Check my VPS and report its project status.',
            'Please inspect our server logs.',
            'Fix the app on my server.',
            'Deploy this website to our VPS.',
            'On my remote host, run the project checks.',
            'Log in to my server and inspect the app.',
            'Build the game on my VPS.',
        )
        for request in requests:
            with self.subTest(request=request):
                self.assertTrue(requests_remote_work(request))

    def test_inference_and_local_tasks_do_not_select_ssh(self):
        requests = (
            'Use my server model to make a game.',
            'Use my server to make me a game.',
            'Use my server to fix my local game.',
            'Use AMD always and fix the local game.',
            'Select the server inference route.',
            'Compare my server model with the local model.',
            'Check my local server.',
            'Make a server application in this project.',
            'How do I fix my VPS?',
            'Explain how to deploy an app to my server.',
            'Research VPS providers.',
            'Use my server model. Fix the game on my Desktop.',
        )
        for request in requests:
            with self.subTest(request=request):
                self.assertFalse(requests_remote_work(request))

    def test_non_text_request_is_not_remote_work(self):
        self.assertFalse(requests_remote_work(None))
        self.assertFalse(requests_remote_work(''))


if __name__ == '__main__':
    unittest.main()
