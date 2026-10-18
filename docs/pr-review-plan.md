# PR #27 review changes — Alfred plan

Status: planned on `feat/omni-jev` (2026-10-17). Implementers: read
[`pr-review-plan-index.md`](pr-review-plan-index.md) first and read only the sections your
task row lists. The kernel half is planned in omni-kernel
(`docs/kernel/pr-review-plan.md` on `feat/model-kinds`, tasks T1–T8); every task here
depends on kernel tasks named in its card.

---

## 1. What is being built, in one screen

The user's review of PR #27 is three comments on `.omni/alfred-builder/agent.yml`:

| Comment | Said | Alfred side |
|---|---|---|
| C1 (line 5) | "Use sonnet-5-5, add opus-5-5, sonnet-5-5, haiku-5-5. Remove the older claude models." | All four manifests name `model: claude-sonnet-5-5`. The table rows and the removal are kernel work (kernel T1). |
| C2 (on line 5, about line 6) | "8192 is almost nothing. This shouldn't be something we are forced to set." | `max_tokens:` is deleted from all four manifests; the kernel now defaults it per model (64 000 for the 5.5 models, kernel T1). |
| C3 (line 10) | "We shouldn't have to set this." (`max_iterations: 30`) | `budget.max_iterations` is deleted from all four manifests; the kernel default is now unbounded (kernel T5). |

The user delegated question Q6 ("should Alfred also drop `budget.tokens/cost`,
`compaction:`, `mode`/`time_aware`, graph `limits`?") to the planner; §2 answers it.

Two approved kernel background items change Alfred's graphs:

- **Kernel T2 (B4):** a terminal node may have outgoing edges; its unrouted outcomes end
  the graph. `device-troubleshoot` no longer needs the `say-bye` agent node (an extra LLM
  call whose only job was to copy the request verbatim): `intake` becomes terminal and
  routes only `identified`.
- **Kernel T3 (B3):** `{{ outputs.<tool_node>.<field> }}` walks into a tool output that is
  a JSON object. `front-door` passes `outputs.resolve.entity_id` / `.entity_name` to
  `apply`/`describe`, which take two plain strings instead of a JSON string they parse.

Out of scope: AlfredCore deletion (omni-jev A9), the Forge path, any live run against real
Anthropic, Jev or Home Assistant, and worktree cleanup.

---

## 2. Decisions

| # | Decision | Reason |
|---|---|---|
| AD1 | `model: claude-sonnet-5-5` in `.omni/alfred`, `alfred-builder`, `alfred-designer`, `alfred-verifier`; `provider: anthropic` stays. | User (C1). |
| AD2 | Delete every `max_tokens:` line. | User (C2); kernel T1 defaults it per model. |
| AD3 | Delete every `budget.max_iterations:` line. | User (C3); kernel T5 makes absent mean unbounded. |
| AD4 | **Keep `budget.cost`** in all four (50.00 / 1.50 / 0.50 / 0.75). **Delete `budget.tokens`** from `.omni/alfred` (20 000 000). | Q6 (planner). A cost cap is the user-meaningful spend guard and works once kernel T1 gives the model prices; the token cap only duplicated it while Claude ids were unpriced. |
| AD5 | `.omni/alfred` `compaction:` becomes `{threshold: 150000, keep_recent: 6}` (absolute tokens instead of `0.7`). | Q6 (planner). Kernel T5 makes this block take effect on the daemon path for the first time. With the 1M window of `claude-sonnet-5-5`, `0.7` lets every request of the resident session grow to ~700 000 input tokens (~$1.40 uncached at $2/MTok) before compacting; 150 000 matches the API's own server-side compaction default and bounds a request near $0.30 uncached. `keep_recent: 6` was the author's choice and is kept. |
| AD6 | Delete `mode: interactive` and `time_aware: basic` from `.omni/alfred` (both restate kernel defaults); keep a one-line comment saying both defaults are what Alfred wants. | Q6 (planner); the review's spirit: do not restate defaults. |
| AD7 | Keep every graph `limits` block (`max_nodes`, `cost_usd`, `wall_clock`). | Q6 (planner). With iterations unbounded inside a node, run limits are the graph's spend and time bound. |
| AD8 | `device-troubleshoot`: `intake` joins `terminal`; edge `identified → inspect` stays; `say-bye` and its two edges are deleted. `intake`'s `gave-up` outcome is renamed `done` (it says the one-line goodbye itself), so the graph's terminal outcomes stay `done`, `new-request` and front-door's `troubleshoot` node is unchanged. | Kernel P18 (B4). The rename keeps the subgraph contract (`tests/test_graphs.py`) and front-door untouched. |
| AD9 | `apply` and `describe` take `entity_id` and `entity_name` (plain strings, both required) instead of `target` (a JSON string). `front-door` passes `{{ outputs.resolve.entity_id }}` / `{{ outputs.resolve.entity_name }}` (and `resolve-read`'s). The permission prompt reads `entity_name` from the args directly. | Kernel P17 (B3). The JSON-in-a-string contract existed only because a tool output could not be indexed (module docstring of `ai_server/mcp/home_assistant_server.py`); an LLM agent calling `apply` also no longer has to reproduce JSON inside a string. |
| AD10 | **Docs rule:** no `Planned` blocks are pre-inserted. Each task edits the doc sections its card lists and adds a `> **As built (pr-review, An).**` note where behaviour changed. | Same simplification as the kernel plan (kernel P20). |
| AD11 | Live checks stay deferred (§8): the Anthropic key returns 401, and no implementer runs a live test. | User decision 6. |

---

## 3. Conventions (every task)

- **Worktree per task.** Branch `pr/A<n>-<slug>` from the *current tip* of `feat/omni-jev`;
  worktree `C:\Users\udayr\Documents\Projects\alfred-pr-A<n>`:

  ```
  git -C C:\Users\udayr\Documents\Projects\alfred worktree add ..\alfred-pr-A<n> -b pr/A<n>-<slug> feat/omni-jev
  ```

  (`feat/omni-jev` is checked out in `C:\Users\udayr\Documents\Projects\alfred-omni`;
  branching from it is fine.) Commit on your branch only. **Never push, never merge.**
- **Python.** Alfred's venv: `C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe`.
  Run everything **from your worktree root**; `ai_server` is not installed, so the cwd copy
  is imported. Check once: `…\python.exe -c "import ai_server; print(ai_server.__file__)"`
  prints your worktree.
- **omni.** `import omni` resolves to `C:\Users\udayr\Documents\Projects\omni-kernel\omni`
  (editable install), i.e. whatever is merged into `feat/model-kinds` in that checkout.
  Your task needs the kernel tasks in its card merged there before you start; check with
  `git -C C:\Users\udayr\Documents\Projects\omni-kernel log --oneline -15` (look for
  `merge: T<n>`). Never edit or `pip install` anything in the kernel.
- **The gate** — must pass before you report; report the counts:

  ```
  C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
  C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m ai_server.omni_bridge.smoke --dry-run
  ```

  Both from your worktree root. Baseline at the plan commit: **387 passed, 10 skipped,
  0 failed** (alfred-omni `6626f26`, kernel `feat/model-kinds` at the kernel plan commit);
  the smoke dry run exits 0. Every task adds tests; none may leave a failure.
- **Live tests** (`@pytest.mark.live`) skip unless `ALFRED_LIVE=1`; do not set it. Do not
  run the smoke without `--dry-run`, and never with `--jev`.
- **Secrets.** Never print, log, `cat`, `type`, echo or commit a key; never open
  `jev.env` (it exists only in the main checkout). Keys are read only through
  `KeyFileCredential`. Tests use a temp file with a fake key. Do not edit `~/.omni/`.
- **Never commit** `jev.env`, `.env`, `.claude/`, `.claude-*/`, `AGENT_COORDINATION.md`,
  `TODO_BACKEND_FIXES.md`, `TODO_CHATUI.md`, `Refresh.md`, `Index_refresh.md`,
  `last_core_output.txt`, `CLAUDE.md`, `.omni/mcp.local.json`. Never `git add -A` or
  `git commit -a`; add files by name. (Tests that write `last_core_output.txt` into the
  worktree are a known nuisance: leave the file uncommitted.)
- **Graph rules** (any task touching `.omni/graphs/`): no house constraints in a graph
  file (`cwd`, `mcp_enabled`, `mcp_config_path`, `on_ask`, `ask_timeout`);
  `tests/test_graphs.py` validates every graph with the kernel validator and the
  library (`discover_library(repo)`).
- **Physical actions always ask.** Do not change any grade in `.omni/mcp.json`; `apply`
  stays `physical`.
- **Docs as you go (AD10).** Edit the doc sections your card lists; add an
  `> **As built (pr-review, An).**` note where behaviour changed; record deviations as
  rows in §9 (`| An | Change to the plan | Why |`). Do not edit another task's sections.
- **Code style.** Type hints, `async` for I/O, `logger = logging.getLogger(__name__)`,
  Pydantic `extra="forbid"` for new API bodies, docstrings that say why; reference this
  plan's decisions (`AD9`) and the kernel's (`kernel P17`) where a reader would wonder.
- **Commit.** Message `<type>(<area>): <summary> (pr-review An)`, body says what and why,
  and ends with exactly:

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UPsCTHToH36PDAphtjSydj
  ```

- **Report** (≤ 25 lines): branch, commit hash(es), gate counts, smoke dry-run exit code,
  docs touched, §9 rows added, anything left undone.

---

## 4. Shared: the four manifests after A1

`.omni/alfred/agent.yml` lines 16–50 become (assembly, tools and skills below L51 are
unchanged):

```yaml
name: alfred

# Sonnet rather than a local model: Alfred's job is tool-calling with a permission gate in
# the middle, and that is the axis local models are weakest on. The output cap and the
# context window come from the kernel's model table (64 000 output tokens, 1M context).
provider: anthropic
model: claude-sonnet-5-5

# Jev (Typesafe) answers the `decide` tool ... (unchanged block, L25-29)
decision:
  provider: typesafe
  model: jev-latest

# Interactive with a `basic` time anchor and clock tool -- both kernel defaults, so neither
# is written here. Autonomous mode is for an agent that wakes itself; a house assistant
# that decides on its own to start doing things is a different product and a worse one.

# The resident session behind a voice client: the cost cap is session-wide, not per task.
# There is no iteration cap (the kernel's default is none); compaction bounds the context.
budget:
  cost: 50.00

# Compact at 150K tokens rather than 0.7 of a 1M window: each request is billed on its
# whole prompt, and a resident session should not carry ~700K tokens per turn (AD5).
compaction:
  threshold: 150000
  keep_recent: 6
```

`.omni/alfred-builder/agent.yml` L4–10 → `provider: anthropic`, `model: claude-sonnet-5-5`,
blank, `budget:` / `  cost: 1.50`. `.omni/alfred-designer/agent.yml` L8–14 → same with
`cost: 0.50`. `.omni/alfred-verifier/agent.yml` L7–13 → same with `cost: 0.75`. All
comments above those lines stay.

---

## 5. Shared: `device-troubleshoot` after A2

`.omni/graphs/device-troubleshoot.json`:

- L6: `"terminal": ["intake", "say-report", "say-escalate"]`.
- `intake` (L9–18): outcomes become

  ```json
  "outcomes": {
    "identified":  "Device and symptom are known; output names both.",
    "new-request": "The person moved on; output is the new request, verbatim.",
    "done":        "The person wants to stop; you said a one-line goodbye."
  },
  ```

  and the prompt's last two sentences become: *"If they move on to something else
  entirely, call emit_outcome with 'new-request' and put that request, in their words, as
  the output; do not answer it — it starts its own conversation. If they want to stop, say
  a one-line goodbye and call emit_outcome with 'done'."* `converse`, `floor_timeout`,
  `inputs` and `constraints` are unchanged.
- Delete the `say-bye` node (L85–92) and the edges at L96–97.
- Everything else unchanged. Terminal outcomes of the graph: `done` (intake, say-report,
  say-escalate) and `new-request` (intake) — exactly front-door's `troubleshoot` node's
  `["done", "new-request"]`.

Runtime consequences (kernel P18): `intake` exiting `identified` continues to `inspect`;
`new-request` or `done` ends the run with that outcome; its implicit `timeout` (15 min
idle floor) or `error` now **ends** the child run with that outcome instead of failing it
("no matching edge"). The bridge's handoff (`transcript.handoff_text`) reads the last
node's output, which is `intake`'s output — no bridge change. The bridge suppresses the
reply text of a terminal speaker that exits `new-request` (`speakers._session_items`), so
`intake`'s last words on a handoff are not spoken and the new conversation's first reply
is (J10) — the same behaviour `say-bye` produced.

---

## 6. Shared: `apply` / `describe` after A3

`ai_server/mcp/home_assistant_server.py`:

- Replace `_parse_target` (L159–179) with:

  ```python
  def _parse_entity(args: Any) -> tuple[dict[str, str] | None, str | None]:
      """``entity_id`` and ``entity_name`` from a call's args (AD9), or ``(None, reason)``."""
  ```

  rules: `entity_id` is a `str` containing `"."` else `"entity_id must be a Home Assistant
  entity id such as light.bedroom_lamp"`; `entity_name` is a non-blank `str` else
  `"entity_name must be the device's name"`. `_apply` (L379–399) and `_describe`
  (L401–) call it instead.
- Schemas: `apply` (L611–648) properties `action`, `entity_id`, `entity_name`, `request`;
  required `["action", "entity_id", "entity_name", "request"]`; `entity_name`'s description
  says it is what a human sees when asked to approve the call. `describe` (L650–670)
  properties `entity_id`, `entity_name`; both required. Tool descriptions drop "the JSON
  string resolve_entity produced" and say "pass the `entity_id` and `entity_name` that
  `resolve_entity` produced". `resolve_entity`'s description (L583–609): "On 'resolved'
  the output holds entity_id, entity_name, domain and confidence; pass entity_id and
  entity_name to apply or describe."
- Module docstring L33–43: the "cannot be indexed ... passed whole" paragraph becomes:
  the graph passes `{{ outputs.resolve.entity_id }}` and `{{ outputs.resolve.entity_name }}`
  (kernel pr-review P17 walks a tool output that is a JSON object), and the permission
  prompt for `apply` shows `entity_name`.

`.omni/graphs/front-door.json`:

```json
{ "id": "say-act", "kind": "tool",
  "capability": "mcp__home_assistant__apply",
  "args": { "action": "{{ outputs.device-action.choice }}",
            "entity_id": "{{ outputs.resolve.entity_id }}",
            "entity_name": "{{ outputs.resolve.entity_name }}",
            "request": "{{ inputs.message }}" } },

{ "id": "say-status", "kind": "tool",
  "capability": "mcp__home_assistant__describe",
  "args": { "entity_id": "{{ outputs.resolve-read.entity_id }}",
            "entity_name": "{{ outputs.resolve-read.entity_name }}" } },
```

(L54–62.) Both nodes are reached only on `resolved`, whose output always carries both
fields (`_resolved_output`).

`ai_server/omni_bridge/asks.py` `_permission_question` (L79–92): for `HA_APPLY`, the name
is `args.get("entity_name") or args.get("entity_id")` (strings, stripped); drop the
`target` parsing. `_json_object` stays (it still parses `payload["args"]`).

`ai_server/mcp/smoke_test.py` L193–209: call `apply` with
`{"action": "turn-on", "entity_id": "switch.coffee_maker", "entity_name": "Coffee Maker",
"request": "make coffee"}` and `describe` with the two fields.

---

## 7. Tasks

All tasks are tier **sonnet**.

### 7.1 A1 — Manifests: Claude Sonnet 5.5, no restated defaults

- **Slug / branch / worktree:** `manifests` · `pr/A1-manifests` · `..\alfred-pr-A1`.
- **Wave 2** (alone). **Depends on:** kernel T1 (5.5 rows with prices) and kernel T5
  (unbounded default; `compaction:` honoured on the daemon path) merged into
  `feat/model-kinds`; Alfred A2 and A3 merged (docs files are shared).
- **Read:** §2 AD1–AD7; §4.
- **Files:** `.omni/alfred/agent.yml`, `.omni/alfred-builder/agent.yml`,
  `.omni/alfred-designer/agent.yml`, `.omni/alfred-verifier/agent.yml`.
- **Tests:** new `tests/test_agent_manifests.py` (`pytest.importorskip("omni")`), for each
  of the four agents loaded with `omni.driver.artifacts.agent.load_agent(name,
  cwd=REPO_ROOT, home=REPO_ROOT)` (as `tests/test_graphs.py:187-193` does) and their raw
  YAML: `provider == "anthropic"`, `model == "claude-sonnet-5-5"`; the model is in
  `omni.driver.provider.anthropic.models.MODEL_METADATA` with `price_input > 0` (so
  `budget.cost` can trip); `agent.max_tokens is None`; `budget.max_iterations` is the
  loader's `ABSENT` sentinel; `budget.cost` is set; the raw YAML has no `max_tokens`,
  `mode`, `time_aware` keys and no `budget.tokens`/`budget.max_iterations`. For `alfred`:
  `compaction.threshold == 150000`, `compaction.keep_recent == 6`, `mode == "interactive"`
  and `time_aware == "basic"` (the defaults). One more: no file under `.omni/` names an
  old Claude id (`claude-(opus|sonnet|haiku)-4`, `claude-3`).
- **Docs:** `docs/graphs.md` — Conventions, the "Agent `alfred` (J17)" bullet (L31–33): add
  that the four manifests name `claude-sonnet-5-5`, set no `max_tokens` or
  `max_iterations` (kernel defaults: per-model output cap, no iteration cap), keep a
  `budget.cost`, and that `alfred` compacts at 150 000 tokens keeping 6 recent turns.
  `docs/omni.md` — Smoke test (L300–359): the bullet at L317 and the live-run note
  L325–327 — the manifests now name `claude-sonnet-5-5`; while the Anthropic key is
  rejected, live runs still use the DeepSeek override. `docs/architecture.md` — only if it
  names a Claude id or the old caps (`grep -n -i "claude\|max_tokens\|max_iterations" docs/architecture.md`).
  Then a final sweep (Alfred has no closing task):
  `git grep -n -E "claude-(opus|sonnet|haiku)-4|max_tokens: [0-9]|max_iterations: [0-9]|say-bye|\"target\": \"\{\{" -- .omni docs README.md`
  must name only `docs/omni-jev-plan.md`, `docs/pr-review-plan.md` or history.
- **Edge cases:** the `decision:` block and the tools allow-list are untouched; YAML
  comments that mention removed keys are rewritten, not left dangling.
- **Acceptance:** gate passes (≥ baseline + new tests); smoke dry-run exits 0.

### 7.2 A2 — `device-troubleshoot` without `say-bye`

- **Slug / branch / worktree:** `troubleshoot-terminal` · `pr/A2-troubleshoot-terminal` ·
  `..\alfred-pr-A2`.
- **Wave 1**, parallel with A3. **Depends on:** kernel T2 merged (the validator accepts
  edges out of a terminal node; `walk` follows them).
- **Read:** §2 AD8; §5.
- **Files:** `.omni/graphs/device-troubleshoot.json`, `.omni/skills/home-graphs/SKILL.md`
  (Conventions, the `new-request` bullet L31–37), `tests/test_graphs.py`.
- **Tests:**
  - `tests/test_graphs.py` — replace `test_bye_is_the_only_new_request_exit_in_troubleshoot`
    (L178–184) with `test_troubleshoot_intake_is_terminal_and_routes_only_identified`:
    `intake` is in `terminal`; its only outgoing edge is `identified → inspect`; no node id
    `say-bye`; `new-request` is declared only by `intake`. Change
    `test_graph_node_outcomes_match_subgraph_terminals` (L163–176): a terminal node
    contributes its declared outcomes **minus those routed by an explicit edge out of it**
    (kernel P18), so `intake` contributes `new-request` and `done`, not `identified`.
    `test_terminal_nodes_speak` keeps passing (`intake` is `converse`).
  - `tests/test_omni_bridge_speakers.py` — new test: in a fixture graph where a `converse`
    node is terminal and routes one outcome onward, the node exiting `new-request` has its
    final assistant text suppressed in `spoken` and present in `walk`; and
    `transcript.handoff_text` returns its output. Build the fixture like the existing ones
    in `tests/omni_fixtures.py` (add a new fixture graph there; do not change the existing
    `mini-trouble` graph, which still tests the `say-` closing-node pattern).
- **Docs:** `docs/graphs.md` — `## device-troubleshoot` (L65–88): terminals line, the
  `intake` row (terminal; leaves by `identified` to `inspect`, else ends the run with
  `done` or `new-request`), delete the `say-bye` row and the "Why `say-bye` exists"
  paragraph, add an `As built (pr-review, A2)` note (one LLM call fewer per hand-off; an
  idle `intake` now ends the run with `timeout` instead of failing it). `docs/omni.md` —
  only if it names `say-bye` (`grep -n say-bye docs/omni.md`).
- **Edge cases:** front-door's `troubleshoot` node (`outcomes: ["done", "new-request"]`) is
  unchanged; a child run that ends `timeout`/`error` reaches front-door as that implicit
  outcome on a terminal `graph` node, which ends the front-door run — confirm with the
  existing bridge tests that an ended run with outcome `timeout` is worded as before
  (`grep -n "timeout" ai_server/omni_bridge/client.py`) and log what you find in §9.
- **Acceptance:** gate passes; smoke dry-run exits 0 (it loads the whole library through
  the kernel validator).

### 7.3 A3 — `apply`/`describe` take `entity_id` and `entity_name`

- **Slug / branch / worktree:** `resolve-fields` · `pr/A3-resolve-fields` · `..\alfred-pr-A3`.
- **Wave 1**, parallel with A2. **Depends on:** kernel T3 merged (`outputs.resolve.<field>`
  resolves at run time).
- **Read:** §2 AD9; §6.
- **Files:** `ai_server/mcp/home_assistant_server.py`, `.omni/graphs/front-door.json`,
  `ai_server/omni_bridge/asks.py`, `ai_server/mcp/smoke_test.py`.
- **Tests:**
  - `tests/test_mcp_ha_resolve.py` — `_target()` (L182) becomes a helper returning the two
    kwargs; `test_apply_takes_resolve_entity_output_whole` (L206) becomes
    `test_apply_takes_resolve_entity_fields` (call `apply` with `output["entity_id"]`,
    `output["entity_name"]` from a real `resolve_entity` result); the bad-target cases
    (L220–240) become bad `entity_id` / blank `entity_name` cases (still change nothing on
    the mock); `test_apply_schema_tells_the_prompt_about_entity_name` (L263) asserts the new
    required lists and the `entity_name` description; describe tests (L277–302) use the
    fields.
  - `tests/test_omni_bridge_asks.py` — `_apply()` (L24) passes `entity_name`/`entity_id`
    args; `test_render_apply_accepts_a_target_object_and_falls_back_to_entity_id` (L58)
    becomes "falls back to `entity_id` when `entity_name` is absent";
    `test_render_apply_with_a_bad_target_falls_back_to_generic` (L64) becomes "no
    `entity_name` and no `entity_id` → the generic question".
  - `tests/test_graphs.py` is **not** edited by A3 (A2 owns it); if you want a front-door
    args test, put it in a new file `tests/test_front_door_args.py`: `say-act` and
    `say-status` args name `outputs.resolve(-read).entity_id` / `.entity_name` and no
    `target`.
  - A blackboard-level check that the substitution really resolves: in the new test file,
    build `omni.kernel.runs.blackboard.Blackboard(outputs={"resolve": "ref"},
    resolver={"ref": json.dumps({"entity_id": "light.x", "entity_name": "X", "domain":
    "light", "confidence": 1.0})}.get)` and assert
    `substitute(say_act_args, bb)` yields `entity_id == "light.x"` and `entity_name == "X"`.
- **Docs:** `docs/omni.md` — `## Home Assistant tools` (L57–99): the tool table's `apply`
  and `describe` rows (Takes: `entity_id`, `entity_name`), replace the "A tool node's
  output cannot be indexed ..." paragraph (L87–90) with the AD9 wording, `As built
  (pr-review, A3)` note. `docs/graphs.md` — `## front-door` table rows `say-act` (L53) and
  `say-status` (L54).
- **Edge cases:** an LLM agent that still sends `target` gets a schema/validation error
  naming the missing fields (acceptable: tools list the new schema); `asks.render` for any
  other tool with an `entity_name` arg is unchanged; `.omni/mcp.json` grades untouched.
- **Acceptance:** gate passes; smoke dry-run exits 0;
  `python -m ai_server.mcp.smoke_test` is **not** required (it starts both MCP servers
  against the mock — run it if it needs no network beyond localhost; report either way).

---

## 8. Merge checklist and deferred live checks

Merge each branch into `feat/omni-jev` in `C:\Users\udayr\Documents\Projects\alfred-omni`
with `git merge --no-ff pr/A<n>-<slug> -m "merge: A<n> <title>"` (plus the attribution
lines), one at a time, the Alfred gate and smoke dry-run after each. Never push.

| After | Gate (passed / skipped / failed) | Smoke dry-run | Kernel `feat/model-kinds` at | Notes |
|---|---|---|---|---|
| plan commit | 387 / 10 / 0 expected | 0 | kernel plan commit | |

- **Before wave 1 (A2, A3):** kernel wave 1 (T1–T4) merged; Alfred gate green against it
  (the kernel merger runs it).
- **Wave 1 (A2, A3):** sources disjoint (A2: `device-troubleshoot.json`, `SKILL.md`,
  `tests/test_graphs.py`, `tests/omni_fixtures.py`, `tests/test_omni_bridge_speakers.py`;
  A3: `front-door.json`, the HA server, `asks.py`, `smoke_test.py`, its tests). Shared doc:
  `docs/graphs.md` (A2 `## device-troubleshoot`; A3 `## front-door` rows) — different
  sections, expect a clean merge.
- **Before wave 2 (A1):** kernel wave 2 (T5, T6) merged and green; A2 and A3 merged.
- **Wave 2 (A1):** check its sweep grep result in the report.
- Do not let the kernel merger move `feat/model-kinds` while an Alfred gate is running.

Deferred (coordinator, needs the user's approval; not run by implementers):

- **LA1 — live smoke on Claude.** Once the Anthropic key works and kernel LC1 has passed:
  the smoke without `OMNI_NODE_PROVIDER`/`OMNI_NODE_MODEL` (so agent nodes use
  `claude-sonnet-5-5`), then with `--jev`. Watch for 400s on replayed thinking blocks and
  for the hand-off scenario 9.3 (now ending at a terminal `intake`).
- **LA2 — live smoke on DeepSeek.** The current live path
  (`OMNI_NODE_PROVIDER=deepseek OMNI_NODE_MODEL=deepseek-v4-flash`, `--jev`) after A1–A3,
  to confirm the graph changes on the provider that works today.

---

## 9. Decision log (implementers append)

| Task | Change to the plan | Why |
|---|---|---|
