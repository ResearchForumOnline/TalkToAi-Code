# TalkToAi Code 8.2.0 — release notes

This update gives a coding task a small, observed starting point before its first model response. It also improves source navigation and rejects invalid native tool-call batches before any call in that batch executes. The app still uses the user's chosen local, server, or optional API model and its existing Plan/Act and tool access settings.

## What changed in source

- **First-turn project work packet.** With automatic project context enabled for a Code task, the controller uses the existing read-only project tools to collect project metadata, a filtered file inventory, and short excerpts from up to four selected files before the model's first response. A compact context setting reduces this to two files and a smaller output budget. User-named paths and relevant manifests are favored. The packet records file identities and hashes, whether excerpts were cut off, partial observations, and detected check commands marked as **not run**. It does not run commands, edit files, or access the network. File content is untrusted task data; the model should read complete source before editing it.
- **Direct-file answer route.** In Plan mode, a narrow request to explain explicitly named source or document files can answer from the first-turn packet when every requested file is uniquely identified and fully observed without redactions or clipping. That turn omits the usual tool schemas, so the model need not request the same reads again. Edits, checks, broader analysis, web research, missing or ambiguous paths, and incomplete packets keep the normal tool loop.
- **Symbol-aware project map.** With a query, `project_map` ranks matching declarations above filename and incidental text matches. It can surface a relevant symbol in a generically named file beyond the former 60-file output cutoff. Candidate source inspection is capped by an 8 MB scan and a two-second scan deadline, and known credential or secret path names are skipped. The returned map still stops at 60 files or its existing output-size bound. This is bounded navigation, not a complete semantic index.
- **Whole-batch validation of native tool calls.** Before executing a native structured call batch, the controller checks every call against the tools currently enabled for that model turn. Disabled names, missing or unexpected arguments, invalid types, and non-finite numeric arguments reject the batch. The agent can ask the model to repair it without executing a partial batch. This is atomic **validation**, not atomic execution: if a later valid tool call fails at runtime, earlier completed actions are not rolled back. Underlying permissions, path checks, and operation-level error handling still apply.
- **More truthful completion and tool recovery.** An Act Code task asking for a change cannot finish as `Ready` on prose alone. A passing local check does not erase a failed browser, server, mail or chain operation; recovery requires a relevant successful retry or an unfinished report. Tool schemas also distinguish fields the adapters truly default, reducing avoidable repair turns.

## Scope and limits

The work packet is orientation, not a project quality assessment or proof that detected checks passed. It may omit files because of inventory, time, output, and privacy limits; follow-up reads remain necessary. Symbol matching is lexical and can rank an unrelated declaration highly if it shares a term. Tool-call validation can prevent malformed batches from partially executing, but it cannot make a weaker model choose the right tools or write correct code. These changes do not rewrite model weights, establish a model speedup, or make TalkToAi equivalent to ChatGPT or Codex.

In one disposable Plan-mode two-file fixture on the configured 30B server route, the direct-file path answered correctly in one model turn, with zero extra file reads, in 39.09 seconds. Two preceding runs that exposed the normal tools made two redundant reads and took 97.22 and 70.25 seconds. Model warm state and run variance were not controlled. This is evidence for that fixture, not a general latency claim or evidence of game-scale coding quality.

## Verification and availability

- Source regression suite: **482 tests ran, 11 skipped, no failures** on the Windows release checkout.
- Windows binary: built with PyInstaller; isolated packaged `--self-test` passed, including startup and existing desktop, browser, chain, process, and policy checks.
- Inno Setup installer: built as `TalkToAi-Code-8.2.0-Windows-Setup.exe`, 66,422,073 bytes, SHA-256 `4607e125948e592acfbe745b8c2e762eed104d022656ba2e40e51d3fab39d01e`.
- [Portable desktop smoke](https://github.com/ResearchForumOnline/TalkToAi-Code/actions/runs/36339718489) passed on both `ubuntu-latest` and `macos-latest` for source commit `6b44fd8aff1ca190ea3325c273e060df912bcd9a`. These jobs install the source, launch the workspace offscreen, and run the portable agent regression set; interactive use still needs separate confirmation.
- GitHub release asset, local installation, and public website deployment are separate outcomes. Check their release, installation and live-page evidence before claiming them complete.

The live model fixture above was read-only. It does not verify game creation, extended autonomous coding, GUI operation, or the quality of an arbitrary model.
