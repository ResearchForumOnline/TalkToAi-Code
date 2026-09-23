# DevSpace folder evaluation and original TalkToAi work

The three supplied Downloads folders (base, LARGE and 3) identify themselves as
DevSpace 1.0.8. A file-by-file SHA-256 comparison found the same 245 files in
their source, documentation and scripts areas. The compared files had identical
hashes. Their README files also had identical hashes. This is not a comparison
of every possible runtime/dependency file in the archives.

## Reuse boundary

Each contains the DevSpace Product Source License, which does not grant copying,
modification or redistribution of product-specific source without a separate
agreement. We evaluated documented capabilities, not their implementations.
No DevSpace source, assets, configuration, branding, dependencies or deployment
scripts were copied into TalkToAi Code. No DevSpace installation scripts ran.
The implementation and tests in this update were written for TalkToAi Code.

## Ideas selected and adaptation

| Documented capability | Original TalkToAi implementation |
| --- | --- |
| Native command sessions with status and cancellation | Turn-owned executable/argument jobs, a native Qt Jobs panel, output cursors, deadlines, bounded buffers and Windows process-tree ownership |
| Budgeted batch project reads with hashes | Up to eight UTF-8 reads per model tool call, character offsets, per-file errors and stale-page detection |
| Explicit artifact exchange | Local output registration in Evidence with labels, size and SHA-256; no download service, upload endpoint or automatic execution |
| Distinguish execution from success | Actual process exit status is displayed separately from model claims and existing check-runner evidence |

## Deliberately not added

No public MCP listener, cloud relay, additional account system, paid-agent SDK,
unrestricted daemon, cross-user workspace service or copied approval system.
Existing local-model, SSH and desktop permissions remain unchanged. These
additions do not establish model quality, successful autonomous gameplay or
Microsoft Store certification.

## Next release gate

Validate the packaged runtime, then separately validate clean installation,
upgrade, shortcuts and uninstall. Preserve the user's active app and data.
Store packaging, licence notices, privacy declarations and certification need
their own review before submission. This feature update is not a Store submission.
