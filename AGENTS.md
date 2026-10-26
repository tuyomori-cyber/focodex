<!-- BEGIN llmctx managed context -->
## LLM Context Resume

- At the start of each new Codex session in this workspace, read `../foxbook/.context/root-todo.md` and then `../foxbook/.context/session.md` before the first substantive response.
- Treat `../foxbook/.context/root-todo.md` as the primary working ToDo for this workspace. It is maintained by `llmctx` as an auto-updated working document, and any merge into a human-managed final ToDo remains manual.
- Treat `../foxbook/.context/session.md` as the shared current-state snapshot for resuming work in this workspace.
- `llmctx` runs `auto-todo` after `Stop`, so check `../foxbook/.context/root-todo.md` when you need the latest working plan.
- If more detail is needed, inspect `../foxbook/.context/turns.jsonl`, then `../foxbook/.context/hook-debug.log`, and then the run logs under `../foxbook/.context/auto-todo`.
- In the first substantive response of each new Codex session, begin with `AGENTS-CHECK: session-md-loaded`.
- In substantive responses, keep the normal user-facing reply natural, but when Now/Next/Relevant changed or became clearer, append a short machine-readable `LLMCTX` block at the end.
- Do not describe those state changes only in prose; when they changed or became clearer, include the `LLMCTX` block in the same reply.
- The `LLMCTX` block should use `<!--LLMCTX` ... `-->` and include only the sections that changed: `Now:`, `Next:`, optionally `Relevant:`.
- When you include `Now:`, prefer concrete current-state bullets using `Done:` and `Pending:` labels rather than abstract commentary.
- Use this exact shape when needed:
  `<!--LLMCTX`
  `Now:`
  `- ...`
  `Next:`
  `- ...`
  `-->`
- Keep each `LLMCTX` section concise with short bullet items, and omit unchanged sections entirely.
- Use Japanese for responses, explanations, and progress updates in this workspace unless the user explicitly asks otherwise.
- When the snapshot indicates cross-repo work, prefer the repo named in the current Relevant section.
<!-- END llmctx managed context -->

## 横断管理台帳

- この変更が二つ以上のFoxbook関連リポジトリ、拡張間契約、共有運用、共通の検証条件に影響する場合は、同じ変更単位で`../foxbook/docs/cross-repo-ledger.md`を更新する。
