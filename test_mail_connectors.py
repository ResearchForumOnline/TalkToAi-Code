"""Offline checks for mail connector boundaries; no account access."""

import base64
import unittest
from unittest.mock import patch

import mail_connectors as mail


class MailConnectorTests(unittest.TestCase):
    def test_gmail_search_bounds_results_and_uses_read_endpoint(self):
        with patch.object(mail, "_gmail_access_token", return_value="mock-token"), \
             patch.object(mail, "_json_request", return_value={"messages": [{"id": "one"}, {"id": "two"}]}) as request:
            result = mail.gmail_search("is:unread", limit=1)
        self.assertEqual(result["messages"], [{"id": "one"}])
        self.assertTrue(request.call_args.args[0].startswith(mail.GMAIL_API + "/messages?"))
        self.assertEqual(request.call_args.kwargs["token"], "mock-token")

    def test_gmail_message_returns_selected_headers_and_bounded_body(self):
        encoded = base64.urlsafe_b64encode(b"a" * 13000).decode()
        source = {"id": "m1", "threadId": "t1", "payload": {
            "headers": [{"name": "Subject", "value": "Hello"},
                        {"name": "X-Secret", "value": "ignored"}],
            "mimeType": "text/plain", "body": {"data": encoded}}}
        with patch.object(mail, "_gmail_access_token", return_value="mock-token"), \
             patch.object(mail, "_json_request", return_value=source):
            result = mail.gmail_get_message("m1")
        self.assertEqual(result["headers"], {"subject": "Hello"})
        self.assertEqual(len(result["body"][0]["text"]), 12000)
        self.assertTrue(result["untrusted_content"])


if __name__ == "__main__":
    unittest.main()
