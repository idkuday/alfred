# Index for pr-review-plan.md

`pr-review-plan.md` (same folder, about 415 lines) is the Alfred half of the user's review
of PR #27: the four agent manifests name `claude-sonnet-5-5` and stop restating
`max_tokens` and `max_iterations`; `device-troubleshoot` drops its `say-bye` node now that a
terminal node may route; `apply`/`describe` take `entity_id` and `entity_name` now that a
graph can read `outputs.resolve.<field>`. The kernel half is
`C:\Users\udayr\Documents\Projects\omni-kernel\docs\kernel\pr-review-plan.md` (tasks T1–T8);
each Alfred task waits for the kernel tasks in its row. Status: planned.

## How to use this index

- **Do NOT read `pr-review-plan.md` in full.** Find your task's row below and read only the
  line ranges it lists, with `Read` and `offset` = start line, `limit` = (end − start + 1).
- Every task reads **§3 Conventions (L57–121)** first: worktree, gate (pytest + smoke
  dry-run), secrets, never-commit list, docs rule, commit attribution, report.
- Decisions are AD1–AD11 in §2. Read the table header (L41–42) plus the rows your task lists
  (AD1 = L43, ADn = L42+n).
- Every task reads **§9 Decision log (L412–end)** first and appends its own rows there.
- Read the doc sections in your row before you change anything and update the same
  sections when done (AD10: no `Planned` blocks; add an `As built (pr-review, An)` note).
- **If line numbers drift**, run `grep -n '^#' <file>` and re-map by heading. Do not read
  the whole file.

## Waves

| Wave | Tasks (parallel inside a wave) | Starts when | Gate before the next wave |
|---|---|---|---|
| 1 | A2, A3 | kernel wave 1 (T1–T4) merged into `feat/model-kinds` | both merged into `feat/omni-jev`; Alfred gate + smoke dry-run green |
| 2 | A1 | kernel wave 2 (T5, T6) merged; A2, A3 merged | merged; Alfred work complete |

Tasks in a wave touch disjoint **source** files; `docs/graphs.md` is shared in wave 1
(different sections). Merger checks: §8 (L379–411). Kernel waves 3–4 (T7, T8) do not
block Alfred.

## Per task

Worktree: `git -C C:\Users\udayr\Documents\Projects\alfred worktree add ..\alfred-pr-A<n> -b pr/A<n>-<slug> feat/omni-jev`.

| Task | Tier | Wave | Parallel with | Depends on | Worktree · branch | Plan sections to read (`pr-review-plan.md`) |
|---|---|---|---|---|---|---|
| A1 Manifests: Sonnet 5.5, no restated defaults | sonnet | 2 | — | kernel T1, T5; A2, A3 | `..\alfred-pr-A1` · `pr/A1-manifests` | Card §7.1 L261–295 · §2 header L41–42 + AD1–AD7 L43–49 · §4 L122–163 |
| A2 `device-troubleshoot` without `say-bye` | sonnet | 1 | A3 | kernel T2 | `..\alfred-pr-A2` · `pr/A2-troubleshoot-terminal` | Card §7.2 L296–333 · §2 header L41–42 + AD8 L50 · §5 L164–199 |
| A3 `apply`/`describe` take `entity_id`, `entity_name` | sonnet | 1 | A2 | kernel T3 | `..\alfred-pr-A3` · `pr/A3-resolve-fields` | Card §7.3 L334–378 · §2 header L41–42 + AD9 L51 · §6 L200–256 |

Background, any task, only if needed: §1 summary L11–38; full §2 L39–56; §8 merge
checklist and deferred live checks L379–411. Kernel decisions the cards cite (P17, P18,
P20) are in the kernel plan's §2 (its index gives the lines).

## Docs sections per task (read before changing, update when done)

Line numbers are as of the plan commit; re-map with `grep -n '^#' <file>` if they drift.

| Task | File — heading (lines) |
|---|---|
| A1 | `docs/graphs.md` — Conventions, "Agent `alfred` (J17)" bullet (L31–33) · `docs/omni.md` — Smoke test (L300–359: L317, L325–327) · `docs/architecture.md` only where it names a Claude id or the old caps |
| A2 | `docs/graphs.md` — device-troubleshoot (L65–88) · `.omni/skills/home-graphs/SKILL.md` — Conventions every graph follows (L25–45: the `new-request` bullet L31–37) |
| A3 | `docs/omni.md` — Home Assistant tools (L57–99: tool table, L87–90) · `docs/graphs.md` — front-door table rows `say-act`, `say-status` (L53–54) |
