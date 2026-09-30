import tempfile
import unittest
from pathlib import Path

from conversation_export import format_conversation, save_conversation


class ConversationExportTests(unittest.TestCase):
    def test_visible_messages_only_and_utf8_save(self):
        task = {
            "title": "Résumé\nResearch",
            "messages": [
                {"role": "user", "content": "Please explain this."},
                {"role": "user", "content": "full extracted attachment text", "display_content": "Attached research.pdf"},
                {"role": "tool", "content": "private tool evidence"},
                {"role": "assistant", "content": "Here is the answer. ✓"},
                {"role": "assistant", "content": "   "},
            ],
            "activity": ["private activity"],
            "config": {"api_key": "private setting"},
        }
        rendered = format_conversation(task)
        self.assertIn("# Résumé Research\n", rendered)
        self.assertIn("## You\n\nPlease explain this.", rendered)
        self.assertIn("## You\n\nAttached research.pdf", rendered)
        self.assertNotIn("full extracted attachment text", rendered)
        self.assertIn("## Assistant\n\nHere is the answer. ✓", rendered)
        self.assertNotIn("private", rendered)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "conversation.md"
            self.assertEqual(save_conversation(task, path), path)
            self.assertEqual(path.read_text(encoding="utf-8"), rendered)

    def test_assistant_protocol_tail_hidden_but_fenced_text_preserved(self):
        task = {"messages": [
            {"role": "assistant", "content": "Visible answer.\n<function=run_command>hidden call"},
            {"role": "assistant", "content": "```text\n<function=example>\n```\nAfter fence."},
            {"role": "assistant", "source": "local", "content": "Local <function=example> text"},
        ]}
        rendered = format_conversation(task)
        self.assertIn("Visible answer.", rendered)
        self.assertNotIn("hidden call", rendered)
        self.assertIn("```text\n<function=example>\n```\nAfter fence.", rendered)
        self.assertIn("Local <function=example> text", rendered)


if __name__ == "__main__":
    unittest.main()
