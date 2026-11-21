import unittest

from agent_focus import (
    FOCUS_CHOICES, focus_guidance, needs_project_kind, normalize_focus, resolve_focus,
    shopping_lookup_request,
)


class AgentFocusTests(unittest.TestCase):
    def test_visible_choices_are_stable_and_old_settings_fall_back_to_auto(self):
        self.assertEqual(FOCUS_CHOICES, (
            ('auto', 'Auto'), ('game', 'Games'), ('coding', 'Coding'),
            ('research', 'Research'), ('writing', 'Writing'), ('shopping', 'Shopping'),
            ('everyday', 'Everyday'),
        ))
        self.assertEqual(normalize_focus('  CODING '), 'coding')
        for value in (None, '', 1, {}, [], 'legacy', 'game development'):
            with self.subTest(value=value):
                self.assertEqual(normalize_focus(value), 'auto')

    def test_explicit_choice_always_overrides_intent_and_project(self):
        for choice, _ in FOCUS_CHOICES[1:]:
            with self.subTest(choice=choice):
                self.assertEqual(resolve_focus(choice, 'Find primary sources on Godot.', project_kind='Godot'), choice)
        self.assertEqual(resolve_focus('legacy', 'Fix the bug in `start.py`.'), 'coding')

    def test_clear_current_tasks_resolve_to_a_specialist(self):
        cases = {
            'Build a 2D game.': 'game',
            'Repair app/scenes/player.native.tscn.': 'game',
            'Fix Unity C# compilation in src/Input.cs.': 'game',
            'Why does project.godot fail to load?': 'game',
            'Write a Python script to replace stale email addresses.': 'coding',
            'Fix src/server.main.ts.': 'coding',
            'Write a function that sorts an array.': 'coding',
            'Fix the C++ runtime error.': 'coding',
            'Find primary sources on Godot collision behavior and write a report.': 'research',
            'Compare published studies about eye tracking.': 'research',
            'Review sources, compare the evidence, and summarize your findings.': 'research',
            'Review source code for likely errors.': 'coding',
            'Write a short email about the Unity editor.': 'writing',
            'Update `README.md` for Godot.': 'writing',
            'Revise the project draft and flag factual gaps.': 'writing',
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(resolve_focus('auto', prompt), expected)

    def test_casual_mentions_and_extension_lookalikes_stay_everyday(self):
        for prompt in (
            '', 'Hi sir, how are you?', 'I played a game last night.',
            'My research is exciting.', 'Make a game plan for my weekend.',
            'What is your favourite game?', 'What is a writing group?',
            'Check the unity of our story.', 'Could you fix `foo.py.backup`?',
            'What happened to the codex?', 'My friend mentioned Godot.',
            'Could you fix `project.godot.backup`?', 'Check `level.tscn2`.',
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(resolve_focus('auto', prompt), 'everyday')

    def test_engine_context_only_breaks_a_tie_for_a_project_task(self):
        for kind in ('godot', ' Unity '):
            with self.subTest(engine=kind):
                self.assertEqual(resolve_focus('auto', 'Improve this scene.', project_kind=kind), 'game')
                self.assertEqual(resolve_focus('auto', 'Test the project.', project_kind=kind), 'game')
                self.assertEqual(resolve_focus('auto', 'Hi there!', project_kind=kind), 'everyday')
                self.assertEqual(resolve_focus('auto', 'Write my résumé.', project_kind=kind), 'writing')
                self.assertEqual(resolve_focus('auto', 'Fix the API endpoint.', project_kind=kind), 'coding')
        self.assertEqual(resolve_focus('auto', 'Improve this scene.', project_kind='project mentions Godot'), 'everyday')
        self.assertEqual(resolve_focus('auto', 'Improve this scene.'), 'everyday')

    def test_only_unresolved_project_actions_need_an_engine_probe(self):
        for prompt in ('Improve this scene.', 'Test the project.'):
            with self.subTest(prompt=prompt):
                self.assertTrue(needs_project_kind(prompt))
        for prompt in ('Hi there!', 'Write an email about the project.', 'Fix the code in this project.'):
            with self.subTest(prompt=prompt):
                self.assertFalse(needs_project_kind(prompt))

    def test_shopping_recognizes_bounded_public_lookup_tasks(self):
        for prompt in (
            'Find a 2 TB SSD under £100.', 'Compare prices and delivery for this monitor.',
            'Show me good deals on headphones.', 'Which laptop should I buy under $1,000?',
            'Where can I buy this model in blue?', 'What’s the cheapest refurbished tablet?',
            'Can you look up prices for this USB cable?',
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(shopping_lookup_request(prompt))
                self.assertEqual(resolve_focus('auto', prompt), 'shopping')
                self.assertEqual(resolve_focus('research', prompt), 'research')

    def test_shopping_lookup_excludes_transactions_and_unrelated_tasks(self):
        for prompt in (
            'Please buy this monitor under £100.', 'Compare prices and purchase it.',
            'Find the best deal and add it to my cart.', 'Find prices, then checkout.',
            'Compare headphones deals and add the cheapest to cart.',
            'Find the cheapest SSD and complete the purchase.',
            'Check my delivery address and prices.', 'Show me the price and payment details.',
            'Check the delivery status of my monitor.', 'Compare prices in my spreadsheet.',
            'Look up the current share price.', 'What is a price floor?',
            'Fix prices in the pricing function.', 'Compare the themes of these two novels.',
            'A new laptop arrived.',
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(shopping_lookup_request(prompt))

    def test_specialists_have_distinct_guidance_and_baseline_adds_nothing(self):
        guidance = {key: focus_guidance(key) for key, _ in FOCUS_CHOICES}
        for key, check in (
            ('game', 'playtest'), ('coding', 'normal entry point'),
            ('research', 'primary sources'), ('writing', 'intended audience'),
            ('shopping', 'current total price'),
        ):
            with self.subTest(focus=key):
                self.assertIn(check, guidance[key])
        self.assertEqual(len(set(guidance.values())), 6)
        self.assertEqual(guidance['auto'], '')
        self.assertEqual(guidance['everyday'], '')
        self.assertEqual(focus_guidance('unknown'), '')


if __name__ == '__main__':
    unittest.main()
