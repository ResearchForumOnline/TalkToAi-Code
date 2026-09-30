# TalkToAi Code 8.5.0

Prepared 30 September 2026 for Microsoft Store package 1.4.0.0.

Private API vault for each user's keys; Groq, Gemini, OpenRouter and Cerebras compatible presets; approved free/self-hosted fallback on quota, rate-limit and temporary transport failures before output. Endpoint cooldowns respect Retry-After and avoid cycling keys/models within a quota-limited provider. Partial responses stop automatic replay. Paid alternatives are excluded; free classification is the user's account declaration, not a verified billing tariff.

Fresh installs start with guided API setup and preserve drafts when setup is cancelled. Existing saved runtime choices remain available. Keys stay session-only or encrypted in the OS credential store, with separate Store/direct vault scope. No developer credentials or fixed free monthly token allowance are included.

API models explicitly marked vision capable can receive a bounded captured screenshot for the following turn. Windows accessibility controls add bounded scrolling and navigation keys, reject moved/replaced/hidden windows and keep physical Escape cancellation when its hook is available. Model activity can show provider-supplied reasoning when enabled. Legacy ZeroThink and Zmail adapters are removed; optional Gmail remains read-only and user configured.

Validation: 560 source tests discovered; 547 passed and 13 were skipped. Loopback HTTP tests cover real 429 failover, model substitution, reported usage, partial tool disconnect without replay, screenshot ordering and missing-vault draft preservation. These tests use dummy credentials and do not establish live account entitlement or universal provider/model compatibility. Packaged runtime and submission status are recorded in MICROSOFT-STORE-STATUS.md.

Provider dashboards control actual allowances, billing and availability. None of the researched documentation establishes a universal five-billion-token free monthly allowance.

- Groq: https://console.groq.com/docs/rate-limits
- Gemini: https://ai.google.dev/gemini-api/docs/rate-limits
- OpenRouter: https://openrouter.ai/docs/api-reference/limits
- Cerebras: https://inference-docs.cerebras.ai/support/rate-limits

## Direct Windows build verification

The direct Windows executable was freshly built from this reviewed 8.5.0 source with PyInstaller 6.21.0 and Python 3.12. Its 55 frozen project modules match the reviewed Store executable's code after normalizing source filenames; the corresponding 55 source files retain the reviewed source bytes. The direct binary contains the API router, document intake, conversation exporter and pypdf modules.

The packaged executable's `--self-test` exited 0 and passed every recorded check, including owned browser interaction, popup handling, screenshot evidence, a managed process, Escape registration, bounded tool chains, offline project inspection and a working starter. Its isolated offscreen Qt preview also completed. Inno Setup built a separate 8.5.0 installer from that fresh direct payload. The portable archive contains the same executable and dependencies, with application and dependency license notices.

These checks verify the packaged runtime and bounded synthetic workflows. The installer was not executed; no clean-profile install, upgrade, manual interactive acceptance, sustained model run or live provider allowance is established. The direct installer is unsigned. The Store uses its separately certified package 1.4.0.0. `SHA256SUMS-8.5.0.txt` records the downloadable installer, portable archive and source archive hashes.
