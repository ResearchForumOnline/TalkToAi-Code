"""Attachment boundaries and user-message integration checks."""
import tempfile
import unittest
from pathlib import Path

from attachment_ingest import MAX_FILE_BYTES, attachment_context, read_attachment


class AttachmentIngestTests(unittest.TestCase):
    def test_local_text_is_bounded_and_labelled_as_untrusted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'notes.md'
            path.write_text('first\n' + 'A' * 13_000, encoding='utf-8')
            item = read_attachment(path)
            self.assertEqual(item['name'], 'notes.md')
            self.assertEqual(len(item['content']), 12_000)
            self.assertTrue(item['truncated'])
            context, names = attachment_context([item])
            self.assertIn('first', context)
            self.assertIn('untrusted reference material', context)
            self.assertIn('notes.md (excerpt truncated)', names)
            self.assertNotIn(str(path), context)

    def test_rejects_key_names_binary_and_oversized_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = root / 'api-token.json'
            secret.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Credential'):
                read_attachment(secret)
            image = root / 'photo.png'
            image.write_bytes(b'\x89PNG\r\n')
            with self.assertRaisesRegex(ValueError, 'not supported'):
                read_attachment(image)
            huge = root / 'large.txt'
            huge.write_bytes(b'x' * (MAX_FILE_BYTES + 1))
            with self.assertRaisesRegex(ValueError, '2 MB'):
                read_attachment(huge)

    def test_pdf_without_selectable_text_reports_the_missing_capability(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scan.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            with path.open('wb') as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ValueError, 'No selectable text'):
                read_attachment(path)


if __name__ == '__main__':
    unittest.main()
