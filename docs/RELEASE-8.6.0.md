# TalkToAi Code 8.6.0

Prepared on 23 October 2026 for Microsoft Store package **1.5.0.0**, using the existing `talktoai.TalkToAiCode-AICodingStudio` identity and existing publisher. Package preparation, Partner Center validation, submission, certification, public availability and installed upgrade are separate states. This document establishes release preparation only.

## Changes

- Meta Model API preset using the official streamed Chat Completions endpoint and the user's own account key. Access and price are account-specific; no free allowance is assumed.
- OpenRouter `openrouter/free` preset automatically selects a free model supporting the request. Metadata listing filters paid model IDs out of a free profile.
- Free-only routing excludes paid, unknown and unverifiable account-labelled-free alternatives. Approved self-hosted endpoints remain available. Runtime validation rejects paid/auto models under an OpenRouter free profile, including when a profile was edited outside the form.
- Free OpenRouter requests require zero prompt/completion/request/image pricing and parameter support. Caller-supplied model fallback lists and paid plugins cannot broaden a free request.
- Existing cooldown, cancellation, incomplete-response protection, document attachment, conversation export, local tools and browser/computer workflows remain included.

See [free routing policy and setup](FREE-API-ROUTING.md) for provider references, privacy, consent and account limitations. This update adds no developer credentials, model weights or promised token allowance.

## Reproducible package preparation

From a clean reviewed source checkout on Windows with the pinned build dependencies and Windows SDK:

```powershell
.\store\build-payload.ps1 -BuildName 8.6.0
.\store\build-msix.ps1 -Version 1.5.0.0
```

The payload build refuses to replace an existing Store payload. It bundles the application, Playwright integration and synthetic Score Arena sample, then includes dependency notices. The package script preserves the Store identity and validates the four-part package version. Configuration, API vault data, conversations and user projects are not build inputs.

## Source verification

The provider feature source passed **580 tests: 567 passed, 13 skipped**. Its portable workflow at `f20cf97dc671674d169a35fe3579ec52a96e0bc6` passed installation, offscreen startup and the selected regression suite on Ubuntu and macOS, including the new free-provider-policy tests. After versioning and package preparation changes, **118 focused release/provider/UI/update tests passed**. A real loopback server verifies request serialization without calling a commercial API.

The freshly built packaged executable passed its runtime self-test with exit code 0. All checks passed, including zero-price policy, actual browser interaction/popup/screenshot, screenshot attachment, a managed process, Escape registration and offline tools. All **55 frozen application modules** matched their reviewed source after normalizing source filenames. Required provider setup, routing, document intake, export and computer/browser modules were present.

Secret scans found no findings in the feature commits or full exported source tree. MakeAppx created the matching 1.5.0.0 candidate with normal manifest validation. A full Windows App Certification Kit run subsequently completed for that exact package: **overall WARNING**, with **22 PASS**, one optional **Blocked executables FAIL** and one mandatory **DPIAwarenessValidation WARNING**. These findings remain for reviewer assessment; this is not a clean WACK pass. The app intentionally exposes user-requested process/command tools, but that fact alone does not independently resolve the static blocked-executable finding. The [sanitized WACK summary](WACK-8.6.0.json) retains all test names and results without raw host logs.

These checks do not prove a clean Store-signed installation, certification or public release. The final package path, identity, size and SHA-256 are recorded in the separate release receipt for the publisher. The source privacy page includes an 8.6 supplement covering Meta, OpenRouter and its selected hosting providers, free-only routing and retention limits. Publishing that source does not establish a deployment of the live privacy website.
