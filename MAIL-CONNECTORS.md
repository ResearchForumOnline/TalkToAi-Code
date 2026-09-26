# Mail connectors in TalkToAi Code

`mail_connectors.py` provides opt-in, read-only Gmail and Zmail functions. The
agent exposes only the named read functions when a task asks about mail. Mail bodies and
subjects are untrusted data; they cannot authorize tool actions.

## Gmail

1. In a Google Cloud project, enable the Gmail API and create an **OAuth
   Desktop app** client. Configure the consent screen and your own account as
   an allowed test user if the app is in testing mode.
2. Click **Connect Gmail** in the desktop app and enter the public client ID
   when prompted. You can instead set `TALKTOAI_GMAIL_CLIENT_ID` in the
   launching process environment. If Google's client configuration includes a client secret, set
   `TALKTOAI_GMAIL_CLIENT_SECRET` as well; keep it out of the model context.
3. The **Connect Gmail** action opens
   the system browser, asks for `gmail.readonly`, receives a localhost callback,
   and saves the token in Windows Credential Manager. It never returns tokens.
4. Use `gmail_search(query, limit)` followed by
   `gmail_get_message(message_id)` for selected results.

The `gmail.readonly` scope is classified as **restricted** by Google. A public
release may need Google's OAuth verification and applicable policy review.
The app does not request `gmail.send` and has no send method.

## Zmail

The existing Zmail MCP service is `https://mail.zmail.my/mcp`. The local
Zmail project says anonymous dynamic OAuth client registration is disabled.
The Zmail operator must register a separate public TalkToAi Code client with:

- redirect URI: `http://127.0.0.1:11743/oauth/callback`
- authorization code grant, public-client token authentication, PKCE S256
- mail service scope `urn:ietf:params:oauth:scope:mail` and `offline_access`
- resource `https://mail.zmail.my`

Click **Connect Zmail** and enter the issued public client ID when prompted,
or set `TALKTOAI_ZMAIL_CLIENT_ID` in the launching process environment. The
action stores the OAuth
token in Windows Credential Manager. `ZmailReadConnector().call(name, args)`
permits only `zmail_list_mailboxes`, `zmail_search_email`,
`zmail_get_message`, and `zmail_get_thread`. If the service rejects a direct
desktop public client or its redirect URI, operator-side registration and
OAuth settings need adjustment. No ChatGPT plugin token is reused.

The local Zmail service configures stateless MCP HTTP with JSON responses.
Its server test sends `initialize` and `tools/list` as separate POST requests
without a session header or initialized notification. This client uses the
same sequence before each `tools/call`. Live production behavior remains to
be checked after a separate OAuth client is registered.

## Integration boundary

Status and read calls are available in Plan and Act when a task asks about mail.
The two connect actions are visible desktop buttons and open a browser sign-in.
Never expose a generic MCP tool name or caller-supplied endpoint to the model.
Keep OAuth responses, Credential Manager values, and authorization headers out
of history, logging, and evidence panels. Sending mail needs a separate
review-and-confirm design before any send method is offered.

Sources: [Google Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[messages.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get),
[desktop loopback OAuth](https://developers.google.com/identity/protocols/oauth2/resources/loopback-migration).
