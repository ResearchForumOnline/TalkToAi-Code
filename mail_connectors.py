"""Opt-in, read-only mail connectors for TalkToAi Code.

Tokens stay in Windows Credential Manager, macOS Keychain or a Linux desktop keyring.
Mailbox content returned by these tools is untrusted task data, never instructions.
"""

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_CREDENTIAL_NAME = "TalkToAiCode:GmailOAuthReadOnly"


def _json_request(url, *, method="GET", token=None, payload=None, timeout=15, headers=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = "Bearer " + token
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("Mail response exceeds the 1 MB limit.")
            return json.loads(data)
    except HTTPError as error:
        # Do not echo remote bodies: OAuth/MCP errors can include sensitive data.
        raise RuntimeError(f"Mail service returned HTTP {error.code}.") from None
    except URLError as error:
        raise RuntimeError("Mail service is unavailable.") from error


def _windows_credential_store():
    if os.name != "nt":
        raise RuntimeError("Gmail local sign-in currently requires Windows Credential Manager.")
    try:
        import win32cred
    except ImportError as error:
        raise RuntimeError("pywin32 is required for Windows Credential Manager.") from error
    return win32cred


def _load_credential(name):
    if os.name != 'nt':
        import keyring
        try:raw=keyring.get_password('TalkToAi Code mail',name)
        except keyring.errors.KeyringError:return None
        if not raw:return None
        try:value=json.loads(raw)
        except (TypeError,ValueError):return None
        return value if isinstance(value,dict) else None
    store = _windows_credential_store()
    try:
        raw = store.CredRead(name, store.CRED_TYPE_GENERIC)["CredentialBlob"]
    except Exception:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _save_credential(name, value):
    if os.name != 'nt':
        import keyring
        keyring.set_password('TalkToAi Code mail',name,json.dumps(value))
        return
    store = _windows_credential_store()
    store.CredWrite({"Type": store.CRED_TYPE_GENERIC, "TargetName": name,
                     "UserName": "TalkToAi Code", "CredentialBlob": json.dumps(value),
                     "Persist": store.CRED_PERSIST_LOCAL_MACHINE}, 0)


def gmail_status():
    """Return configuration state without making a mailbox or token request."""
    client_id = os.environ.get("TALKTOAI_GMAIL_CLIENT_ID", "").strip()
    token = _load_credential(_CREDENTIAL_NAME) if client_id else None
    return {"configured": bool(client_id), "connected": bool(token and token.get("refresh_token")),
            "scope": GMAIL_SCOPE,
            "setup": None if client_id else "Set TALKTOAI_GMAIL_CLIENT_ID to a Google Desktop OAuth client ID with Gmail API enabled."}


def gmail_connect(timeout=120):
    """Launch Google's system-browser desktop OAuth flow on an explicit action.

    This never presents an access or refresh token to the model. The callback
    only accepts a single localhost request with the generated state value.
    """
    client_id = os.environ.get("TALKTOAI_GMAIL_CLIENT_ID", "").strip()
    if not client_id:
        raise ValueError("Set TALKTOAI_GMAIL_CLIENT_ID to a Google Desktop OAuth client ID first.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            if urlsplit(self.path).path != "/oauth/callback" or query.get("state") != [state]:
                self.send_error(400, "Invalid OAuth callback")
                return
            result["code"] = (query.get("code") or [None])[0]
            result["error"] = (query.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"TalkToAi Code authorization received. You may close this tab.")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}/oauth/callback"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": GMAIL_SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    webbrowser.open(url)
    deadline = time.monotonic() + min(max(int(timeout), 1), 180)
    try:
        while "code" not in result and "error" not in result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.get("error"):
        raise RuntimeError("Google authorization was declined or failed.")
    if not result.get("code"):
        raise TimeoutError("Google sign-in timed out. Try Connect Gmail again.")
    form_values = {"client_id": client_id, "code": result["code"],
                      "code_verifier": verifier, "redirect_uri": redirect,
                      "grant_type": "authorization_code"}
    if os.environ.get("TALKTOAI_GMAIL_CLIENT_SECRET"):
        form_values["client_secret"] = os.environ["TALKTOAI_GMAIL_CLIENT_SECRET"]
    form = urlencode(form_values).encode()
    request = Request("https://oauth2.googleapis.com/token", data=form,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urlopen(request, timeout=15) as response:
            token = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Google token exchange returned HTTP {error.code}.") from None
    if not token.get("refresh_token") or not token.get("access_token"):
        raise RuntimeError("Google did not provide a refresh token; reconnect with consent.")
    _save_credential(_CREDENTIAL_NAME, {"refresh_token": token["refresh_token"], "access_token": token["access_token"],
                       "expires_at": time.time() + int(token.get("expires_in", 3600)) - 60,
                       "client_id": client_id, "scope": GMAIL_SCOPE})
    return {"connected": True, "scope": GMAIL_SCOPE}


def _gmail_access_token():
    token = _load_credential(_CREDENTIAL_NAME)
    client_id = os.environ.get("TALKTOAI_GMAIL_CLIENT_ID", "").strip()
    if not token or token.get("client_id") != client_id or token.get("scope") != GMAIL_SCOPE:
        raise RuntimeError("Gmail is not connected. Use Connect Gmail first.")
    if token.get("access_token") and token.get("expires_at", 0) > time.time():
        return token["access_token"]
    form_values = {"client_id": client_id, "refresh_token": token["refresh_token"],
                   "grant_type": "refresh_token"}
    if os.environ.get("TALKTOAI_GMAIL_CLIENT_SECRET"):
        form_values["client_secret"] = os.environ["TALKTOAI_GMAIL_CLIENT_SECRET"]
    form = urlencode(form_values).encode()
    request = Request("https://oauth2.googleapis.com/token", data=form,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urlopen(request, timeout=15) as response:
            refreshed = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Gmail token refresh returned HTTP {error.code}; reconnect Gmail.") from None
    if not refreshed.get("access_token"):
        raise RuntimeError("Gmail token refresh failed; reconnect Gmail.")
    token.update(access_token=refreshed["access_token"],
                 expires_at=time.time() + int(refreshed.get("expires_in", 3600)) - 60)
    _save_credential(_CREDENTIAL_NAME, token)
    return token["access_token"]


def gmail_search(query, limit=10):
    limit = max(1, min(int(limit), 25))
    params = urlencode({"q": str(query)[:500], "maxResults": limit})
    data = _json_request(GMAIL_API + "/messages?" + params, token=_gmail_access_token())
    return {"messages": data.get("messages", [])[:limit],
            "nextPageToken": data.get("nextPageToken"),
            "note": "Message IDs only. Call gmail_get_message for selected items."}


def _decode_gmail_text(part):
    result = []
    if part.get("mimeType") in ("text/plain", "text/html"):
        encoded = (part.get("body") or {}).get("data")
        if encoded:
            try:
                raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                result.append({"mimeType": part["mimeType"], "text": raw.decode("utf-8", "replace")[:12000]})
            except (ValueError, TypeError):
                pass
    for child in part.get("parts", [])[:20]:
        result.extend(_decode_gmail_text(child))
    return result[:5]


def gmail_get_message(message_id):
    if not isinstance(message_id, str) or not message_id or len(message_id) > 200 or "/" in message_id:
        raise ValueError("Invalid Gmail message ID.")
    data = _json_request(GMAIL_API + "/messages/" + quote(message_id, safe="") + "?format=full",
                         token=_gmail_access_token())
    payload = data.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "")[:2000]
               for h in payload.get("headers", []) if h.get("name", "").lower() in
               ("from", "to", "cc", "date", "subject", "message-id")}
    return {"id": data.get("id"), "threadId": data.get("threadId"),
            "labelIds": data.get("labelIds", []), "headers": headers,
            "body": _decode_gmail_text(payload), "snippet": data.get("snippet", "")[:2000],
            "untrusted_content": True}
