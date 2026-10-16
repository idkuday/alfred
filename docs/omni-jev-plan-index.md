# Index for omni-jev-plan.md

`omni-jev-plan.md` (same folder, about 1165 lines) is the plan for the `feat/omni-jev`
branch: Alfred as a graph-bodied system on omni-kernel with Jev deciding the path.

## How to use this index

- **Do NOT read `omni-jev-plan.md` in full.** Find your task's row below and read only the
  line ranges it lists, with `Read` and `offset` = start line, `limit` = (end − start + 1).
- Every task reads **§3 Conventions (L93–144)** first: worktree, gate, secrets, docs rule.
- Decisions are numbered J1–J23 in §2. Read the table header (L65–66) plus the rows your
  task lists; each row is one line.
- Every task appends to **§12 Decision log (L1160–end)**; read its rows first, they record
  deviations made before you.
- Read the doc sections in your row before you change code; update the same sections when
  you are done (flip their `Planned (omni-jev, An)` blocks to `As built (omni-jev, An)`).
- **If line numbers drift** (the plan or a doc was edited after this index was written),
  run `grep -n '^#' <file>` and re-map by heading. Do not read the whole file.

## Waves

| Wave | Tasks (parallel inside a wave) | Gate before the next wave |
|---|---|---|
| 0 | A0 (coordinator), A1 | A0 verified (protocol 8); A1 merged |
| 1 | A2, A3, A4 | all three merged |
| 2 | A5, A6 | both merged |
| 3 | A7, A8 | both merged; coordinator runs A7 live |
| 4 | A9 (coordinator may defer) | merged |
| 5 | A10 (coordinator, local file) | done |

Tasks in one wave touch disjoint **source** files. Docs files are shared across a wave
(`docs/omni.md` in waves 1 and 2) but each task edits only its own sections.

## Per task

| Task | Tier | Wave | Parallel with | Depends on | Plan sections to read (`omni-jev-plan.md`) |
|---|---|---|---|---|---|
| A0 | coordinator | 0 | A1 | kernel T10 merged | §4.1 L147–162 · J23 L89 |
| A1 | haiku | 0 | A0 | — | Card §10.1 L945–981 · §2 L65–66 + J21 L87, J22 L88 · §12 L1160–end |
| A2 | sonnet | 1 | A3, A4 | A0, A1 | Card §10.2 L982–1007 · §2 L65–66 + J4 L70, J5 L71, J7 L73, J16 L82 · §4.2 L163–189 · §6 L558–653 · §12 L1160–end |
| A3 | sonnet | 1 | A2, A4 | A0, A1 | Card §10.3 L1008–1032 · §2 L65–66 + J3 L69, J6 L72, J8 L74, J9 L75, J10 L76, J12 L78, J16 L82, J17 L83, J20 L86 · §4.2 L163–189 · §5 L190–557 · §12 L1160–end |
| A4 | sonnet | 1 | A2, A3 | A0, A1 | Card §10.4 L1033–1051 · §2 L65–66 + J6–J8 L72–74, J10–J14 L76–80 · §5.1 L196–216 · §7.1 L656–674 · §7.3–§7.7 L700–819 · §7.9 L844–852 · §9 L877–935 · §12 L1160–end |
| A5 | sonnet | 2 | A6 | A2, A3, A4 | Card §10.5 L1052–1070 · §2 L65–66 + J1 L67, J2 L68, J10 L76, J11 L77, J13 L79, J16 L82, J18 L84, J19 L85 · §4.2 L163–189 · §7 L654–852 · §9 L877–935 · §12 L1160–end |
| A6 | sonnet | 2 | A5 | A1, A4 | Card §10.6 L1071–1085 · §2 L65–66 + J1 L67, J11 L77, J13 L79, J15 L81, J19 L85 · §4.2 L163–189 · §7.2 L675–699 · §7.3 L700–739 · §8 L853–876 · §12 L1160–end |
| A7 | sonnet | 3 | A8 | A5, A6 | Card §10.7 L1086–1100 · §6.4 L644–653 · §7.2 L675–699 · §9 L877–935 · §12 L1160–end |
| A8 | haiku | 3 | A7 | A1–A6 | Card §10.8 L1101–1114 · §4.1 L147–162 · J22 L88 · §12 L1160–end |
| A9 | sonnet | 4 | — | A7 live pass | Card §10.9 L1115–1128 · J15 L81 · §12 L1160–end |
| A10 | haiku | 5 | — | A8 (A9) | Card §10.10 L1129–1140 · J22 L88 |

Background, any task, only if needed: §1 summary L16–59; full §2 Decisions L60–92;
§11 out of scope L1141–1159.

## Docs sections per task (read before coding, update when done)

| Task | File — heading (line) |
|---|---|
| A1 | `docs/architecture.md` — Configuration (L31–42) |
| A2 | `docs/omni.md` — MCP servers (L14–23) · Home Assistant tools (L24–29) |
| A3 | `docs/graphs.md` — whole file (L1–end, 41 lines) · `.omni/skills/home-graphs/SKILL.md` (whole, 49 lines) |
| A4 | `docs/omni.md` — Conversations (L30–37) · What Alfred says (L38–42) · Asks (L43–48) |
| A5 | `docs/omni.md` — The bridge (L49–54) · Restart (L55–59) |
| A6 | `docs/architecture.md` — Shape (L10–30) · HTTP API (L43–54) |
| A7 | `docs/omni.md` — Smoke test (L60–64) |
| A8 | every `Planned (omni-jev` block: `grep -rn "Planned (omni-jev" docs README.md` · `README.md`, `QUICKSTART.md`, `ARCHITECTURE.md`, `CODEBASE_SUMMARY.md` (tops only) |
| A9 | `docs/architecture.md` — Shape (L10–30) |
| A10 | `C:\Users\udayr\Documents\Projects\alfred\CLAUDE.md` (main checkout, git-ignored) |
