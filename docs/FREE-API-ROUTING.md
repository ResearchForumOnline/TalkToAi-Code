# Meta and automatic free API routing

The 23 October 2026 source update expands the existing private API vault. It does not replace the separately reviewed Windows binaries or Microsoft Store package.

## Automatic free model selection

1. Open **Private API vault & models** and choose **OpenRouter free models**.
2. Supply your own OpenRouter API key, either session-only, through the encrypted system credential store, or through `OPENROUTER_API_KEY`.
3. The suggested model, `openrouter/free`, selects an available free model with the capabilities your request needs. You may instead use **Fetch models** and select a named `:free` variant. This preset filters paid IDs out of the catalog.
4. Save the profile, then choose **Use selected API**. Saving a profile or fetching model metadata does not run inference.

The preset selects **Require free-only routing**. Every OpenRouter profile marked `free` must use `openrouter/free` or a `:free` model. This is checked on the runtime path as well as in the form, including for manually edited profiles. Each free OpenRouter request supplies zero ceilings for prompt, completion, per-request and image pricing. It requires support for supplied request parameters, preserves tool schemas and removes caller-supplied model fallback lists and paid plugins.

The free router changes the underlying model over time. Its selection is not a reproducible research model choice or a quality guarantee. For experiments, choose a named model and retain the actual provider/model usage evidence and conditions.

## Optional fallback between approved endpoints

Enable **Allow automatic fallback** on the selected profile and each alternative you permit to receive the conversation, project context and tool results. With **Require free-only routing** enabled on the selected profile, the app can try only approved OpenRouter free models or endpoints you identify as self-hosted. It excludes paid profiles, unknown profiles and other cloud profiles merely labelled `free`. Known direct cloud endpoints cannot be relabelled `self_hosted` to pass this check. Self-hosted infrastructure can have its own hosting or electricity costs.

Fallback happens only before any partial response, after an eligible quota, rate-limit or availability failure. Existing endpoint cooldowns, cancellation and partial-response protection remain in force. When no eligible endpoint is available, the app stops and reports the failure. It does not purchase credits, rotate account keys to bypass quota, or broaden this policy to a paid API.

Existing profiles retain their stored fallback choices; the new `free_only` property defaults to false for older profiles. Other providers' account-based free tiers remain available through their existing presets, but an account label cannot impose a zero-price ceiling on those providers. Check provider-side billing limits before using them. Paid primary profiles remain usable when explicitly selected without free-only policy. A token output limit is not a money cap.

## Meta Model API

Choose **Meta Model API** to fill `https://api.meta.ai/v1`, the `MODEL_API_KEY` environment reference and the model ID shown in Meta's current compatibility example, `muse-spark-1.3`. Fetch models or replace the ID according to your account's model access. This app uses the streamed Chat Completions compatibility endpoint with its existing message/tool adapter. Meta's Responses-specific features and cross-turn reasoning state are not implemented by this preset.

The Meta preset starts with unknown cost tier and fallback disabled. No free allowance is assumed. The app requires a key for Meta and OpenRouter, and rejects insecure or incorrect paths for these known endpoints. API keys stay separate from profile metadata, Git, conversation history and exported transcripts.

## Official provider references checked for this update

- [OpenRouter free models router](https://openrouter.ai/openrouter/free) describes automatic free selection and capability filtering.
- [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection) documents `max_price` prompt, completion, request and image ceilings and `require_parameters`.
- [Meta SDK compatibility](https://dev.meta.ai/docs/sdks) documents the base URL, account key and compatible clients.
- [Meta Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions) documents streaming, message roles and the example model.
- [Groq compatibility](https://console.groq.com/docs/overview), [Gemini compatibility](https://ai.google.dev/gemini-api/docs/openai) and [Cerebras Chat Completions](https://inference-docs.cerebras.ai/api-reference/chat-completions) describe the other available direct-provider routes.

Availability, account eligibility, privacy terms and quotas can change. No allowance, live inference access or charged provider request was used to verify this update. Regression checks use disposable profiles, mocked metadata and a real local HTTP streaming server; they do not demonstrate provider availability or a clean-device desktop upgrade.

Local verification completed on 23 October 2026: **580 tests discovered, 567 passed, 13 skipped**, in 114.794 seconds. New coverage verifies strict fallback exclusion, exhaustion without a paid call, free-router eligibility, rejected paid primary models, zero-price fields on a real serialized streaming request, safe preset defaults, catalog filtering and profile persistence. The provider dialog was also rendered and inspected with a disposable profile. Skipped platform/live checks are not evidence of a live provider integration or installed-app upgrade.
