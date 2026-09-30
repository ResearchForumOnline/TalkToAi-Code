# Mail connectors in TalkToAi Code

`mail_connectors.py` provides opt-in, read-only Gmail functions. The
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
   and saves the token in the operating system credential store. It never returns tokens.
4. Use `gmail_search(query, limit)` followed by
   `gmail_get_message(message_id)` for selected results.

The `gmail.readonly` scope is classified as **restricted** by Google. A public
release may need Google's OAuth verification and applicable policy review.
The app does not request `gmail.send` and has no send method.

## Integration boundary

Status and read calls are available in Plan and Act when a task asks about mail.
The visible Connect Gmail button opens a browser sign-in.
Never expose a generic MCP tool name or caller-supplied endpoint to the model.
Keep OAuth responses, Credential Manager values, and authorization headers out
of history, logging, and evidence panels. Sending mail needs a separate
review-and-confirm design before any send method is offered.

Sources: [Google Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[messages.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get),
[desktop loopback OAuth](https://developers.google.com/identity/protocols/oauth2/resources/loopback-migration).
