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

    def test_zmail_blocks_any_write_tool_before_accessing_token(self):
        def forbidden_token():
            raise AssertionError("token must not be loaded")
        connector = mail.ZmailReadConnector(forbidden_token)
        with self.assertRaises(PermissionError):
            connector.call("zmail_send_draft", {"id": "draft"})

    def test_zmail_initializes_then_calls_exact_read_tool(self):
        with patch.object(mail, "_json_request", side_effect=[
            {"result": {"protocolVersion": "2025-06-18"}},
            {"result": {"content": [{"type": "text", "text": "mock"}]}}]) as request:
            result = mail.ZmailReadConnector(lambda: "mock-token").call(
                "zmail_search_email", {"query": "from:example.test", "limit": 1})
        self.assertTrue(result["untrusted_content"])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].kwargs["payload"]["method"], "initialize")
        self.assertEqual(request.call_args_list[1].kwargs["payload"]["method"], "tools/call")
        self.assertEqual(request.call_args_list[1].kwargs["payload"]["params"]["name"], "zmail_search_email")


if __name__ == "__main__":
    unittest.main()
