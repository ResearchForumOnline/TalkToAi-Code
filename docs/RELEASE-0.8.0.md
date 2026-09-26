# TalkToAi Code 0.8.0

## Visible computer control you can cancel

- Desktop actions display **TalkToAi is using your computer**, the current action and a Stop button in a floating panel.
- Browser actions display **TalkToAi is working in its browser**: browser automation uses a separate app-owned session.
- Physical Escape cancels a busy task on Windows even when another app is foreground. The temporary hook ignores injected keyboard actions, stores no keystrokes, and is removed when the task finishes.
- On other platforms or failed hook registration, the panel clearly describes Escape inside TalkToAi and the Stop button.
- The panel stays above other windows without taking keyboard focus. Its labels omit typed text, search queries and URLs.
- Stopping remains visible until the worker finishes. Explicit Stop/Escape returns queued steering to the draft and prevents accidental restart, including cross-thread event races.
- Closing the active panel requests Stop. Idle Escape behavior in dialogs is unchanged.

![Computer-control panel](../assets/screens/control-overlay-080.png)

The image is a rendered UI fixture, not evidence of a live computer task. Cancellation stops further work; it cannot undo an action already delivered. In-flight browser actions may take time to yield. Windows native control remains Windows-only; browser tools are portable.

## Browser workflow repairs

Accessible control names and newly opened pages are covered by local browser workflow acceptance checks. Verification details are recorded below after the release build.

## Verification

Release verification is in progress; do not treat this working document as a published release.
