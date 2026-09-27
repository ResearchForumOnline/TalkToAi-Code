import unittest

from reasoning_stream import ReasoningActivity


class ReasoningActivityTests(unittest.TestCase):
    def test_sparse_events_do_not_expose_provider_text(self):
        tracker = ReasoningActivity(interval=8)
        self.assertIsNone(tracker.feed(""))
        first = tracker.feed("secret=X")
        self.assertEqual(first["characters"], 8)
        self.assertNotIn("secret", repr(first))
        self.assertIsNone(tracker.feed("123"))
        final = tracker.finish()
        self.assertEqual(final["characters"], 11)
        self.assertFalse(final["active"])

    def test_absent_reasoning_is_not_claimed(self):
        tracker = ReasoningActivity()
        self.assertIsNone(tracker.feed(None))
        self.assertIsNone(tracker.feed(123))
        self.assertIsNone(tracker.finish())

    def test_opt_in_excerpt_is_bounded_and_filters_common_credentials(self):
        tracker = ReasoningActivity(interval=1, include_excerpt=True)
        event = tracker.feed("Investigate. api_key=abc123 <tool_call> " + "x" * 600)
        self.assertLessEqual(len(event["excerpt"]), 240)
        self.assertNotIn("abc123", event["excerpt"])
        self.assertNotIn("<tool_call>", event["excerpt"])


if __name__ == "__main__":
    unittest.main()
