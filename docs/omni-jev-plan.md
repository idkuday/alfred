# Alfred on Omni + Jev — plan

> Status: **plan; nothing below is built yet.** Branch `feat/omni-jev` (from `develop` @
> `4b12c6b`). Every doc section this plan changes carries a
> `> **Planned (omni-jev, An).**` block; the task that builds it flips the block to
> `> **As built (omni-jev, An).**` and corrects whatever the build taught.
>
> Motto, inherited from the kernel: **the kernel lets you build a custom system around it
> — mechanisms, not policy.** omni-kernel (branch `feat/model-kinds`) supplies decide
> nodes, conversational nodes, the journal and the run plane. Everything in this plan is
> Alfred's *policy* over those mechanisms. Nothing here edits omni-kernel.
>
> **Implementers: start from `omni-jev-plan-index.md`** — it lists which sections of this
> file your task needs. Do not read this file in full.

## 1. What is being built, in one screen

1. **Alfred is a graph-bodied system.** Every user conversation is **one run** of the
   `front-door` graph (`.omni/graphs/front-door.json`). The conversation id the HTTP API
   hands out *is* that run's id.
2. **Jev decides the path.** The first node is a TypeSafe Jev `decide` node that sorts the
   request into `device` / `troubleshoot` / `routine` / `new-capability` / `chat`. A second
   decide node (`device-action`) picks the device verb. A plain device command ("turn on
   the bedroom lamp") then runs **without any chat model**: Jev → Jev → an entity-resolution
   tool (Jev again, inside Alfred's Home Assistant MCP server) → the action tool → a
   one-line reply.
3. **Agents that talk hold the floor.** Chat, device help, clarification and the
   troubleshoot intake are `converse` agent nodes: they keep their session across user
   turns until they call `emit_outcome`. Jev never sees that chat — every node declares
   its own context.
4. **When the graph exits, the conversation is over.** The only way back is **loading an
   earlier conversation**: Alfred reads the old run's transcript out of the journal and
   starts a *new* front-door run seeded with it (`inputs.earlier`). The kernel adds
   nothing for this (kernel M22). A converse node that hears a genuinely new request ends
   its run with outcome `new-request`; Alfred then starts the follow-on conversation the
   same way (a *handoff*).
5. **Entity resolution with Jev.** "the light" → `light.bedroom_lamp` is a Jev `choice`
   over the live Home Assistant entity list: inside the HA MCP server for the fast path
   (`resolve_entity`), and via the kernel's `decide` capability for LLM agents
   (`agent.yml` `decision:`).
6. **Classify-only graph nodes become Jev.** In `device-troubleshoot` and `home-routine`,
   an agent that gathered evidence *and* chose an outcome is split into an evidence agent
   plus a Jev decide node, with an `unsure`/`error`/`timeout` fallback to an LLM agent that
   chooses the old way.
7. **FastAPI is an Omni client.** `ai_server/omni_bridge/` connects to `omnid` over the
   native codec, hosts the graph policy in-process (`GraphHost`), answers permission and
   approval asks from the user's next message (interpreted by Jev), and serves the
   existing `/execute` and `/sessions` endpoints from the journal. **AlfredCore and the
   SQLite session store are retired** (last wave).
8. **The Omni fixes Alfred owed** (Refresh.md §4.1): MCP servers start with Alfred's venv
   Python and the repo as cwd; plugin tool grades use the prefixed names; graph nodes get
   the constraints they need; `jev.env` is git-ignored.

Out of scope: the voice client as a native-codec client (migration 5.4) and moving
`alfred-ui` to AG-UI (5.5) — the existing HTTP API stays the UI's surface; any omni-kernel
change (follow-ups are listed in §11).

---

## 2. Decisions

Each row: the call, why, and the alternative rejected. Implementers who must deviate append
to §12 instead of editing this table.

| # | Decision | Rationale | Alternative rejected |
|---|---|---|---|
| J1 | **One conversation = one run of `front-door`**; the API's `session_id` is the run id (`run-…`). | The kernel's run is already journaled, resumable, listable (`list_runs`) and ends exactly when the graph does — the user's rule "graph exits ⇒ conversation over" falls out. | A long-lived Alfred session that starts graphs via `run_graph`: two lifecycles to reconcile, and the conversation would outlive the graph. |
| J2 | **The FastAPI process hosts the graph policy** (`omni.graph.host.GraphHost` + `start_graph_run`) over one `NativeClient` from `omni.cli.connect.open_client(spawn=True)`. The daemon (`omnid`) runs the nodes. | This is the kernel's own out-of-process host (D7, task 3.3); Alfred owns its graph library and can rewrite node constraints before submission (J6). No privileged access. | Asking the daemon to host the graph (`run_graph` from a session): the library would be resolved against the daemon's cwd, and Alfred could not inject constraints. |
| J3 | **Front-door is a chain of two Jev decide nodes** (`front` → `device-action`), not one nine-way question. | Matches the user's "a chain of Jevs before a chat model"; each question stays small and well-described; the cost is one extra ~150 ms call on the device path only. | One decide node with every verb × category as outcomes: one call saved, but the descriptions blur and `threshold` means less. |
| J4 | **The LLM-free device path** is: `device-action` → tool `mcp__home_assistant__resolve_entity` (Jev inside the MCP server) → tool `mcp__home_assistant__apply` (physical, asks) or `…__describe` (read). | A tool node's output is a string that cannot be indexed (`outputs.resolve.entity_id` fails — kernel §10 T6 row), so structured data is passed **whole**: `"target": "{{ outputs.resolve }}"` keeps the JSON string, and `apply`/`describe` parse it. The permission prompt then shows the resolved device. No kernel change. | (a) Resolve and act in one tool: the physical ask would fire before the user can see *which* device. (b) A kernel change making JSON tool outputs indexable: out of scope here; listed in §11. |
| J5 | **Entity resolution inside the MCP server uses omni-kernel as a library** (`omni.driver.provider.TypeSafeDecision` with a `KeyFileCredential` on `settings.jev_key_file`), via one helper `ai_server/decisions.py`. With no key file it degrades: `resolve_entity` returns `ambiguous` and the graph falls back to the LLM device agent. | A system built around the kernel may use its library; one vendor client, one credential class, no copy of the HTTP code. | Calling the Jev HTTP API directly from Alfred: a second client to maintain. |
| J6 | **House constraints are injected by the bridge, not written in the graph files.** On load, every `agent` and `tool` node (including nodes of subgraphs) gets `setdefault` for `cwd` (repo root, absolute), `mcp_enabled: true`, `mcp_config_path` (the generated `.omni/mcp.local.json`), `on_ask: "prompt"`, `ask_timeout` (`settings.omni_ask_timeout`), and `provider`/`model` when `settings.omni_node_provider`/`omni_node_model` are set. Decide nodes get nothing (their `constraints.provider/model` are in the file). | Machine paths never enter a committed file; `omni graph validate` still validates the files as written; one place to change node defaults. `on_ask: "prompt"` is what relays a node's permission ask onto the run stream (supervisor `_spec` docstring, D76). | Writing absolute paths into `.omni/graphs/*.json`: machine-specific, and the user cannot share the repo. |
| J7 | **`.omni/mcp.local.json` is generated at bridge start-up** from `.omni/mcp.json`: each server's `command` becomes `sys.executable` (Alfred's venv Python) when it is `"python"`, and `cwd` becomes the absolute repo root. It is git-ignored. The committed `mcp.json` gains `"cwd": ".."` (resolved relative to the file, kernel-supported) for `omni --mcp` use from a shell. | The daemon spawns MCP servers; today it uses whatever `python` is on *its* PATH and its own cwd, so 0 MCP tools connect (Refresh §4.1.1). | Committing an absolute interpreter path. |
| J8 | **Who speaks to the user** (what the bridge returns as Alfred's reply) is a naming rule: a node speaks iff it is `converse: true` **or** its id starts with `say-`. A speaking agent node contributes its assistant text; a speaking tool node contributes its output text. A test lints every graph for it. | Explicit, greppable, needs no kernel field and no side file. Evidence-gathering agents (inspect, recover, read-back, build…) stay silent, so the user hears one voice. | Every agent's text is spoken: the user would hear the diagnosis notes of five internal agents. A side config file mapping graphs → speakers: drifts from the graphs. |
| J9 | **Terminal nodes speak or converse; there are no "done" nodes.** Converse nodes are terminal in `front-door`; the run's result is the terminal node's own `NodeExited` outcome (the `RunEnded.outcome` is the run's, e.g. `completed`). | Removes the kernel example's `chat → done` silliness; fewer LLM sessions. | A `done` agent per path: an extra LLM call to say "ok". |
| J10 | **Handoff:** a converse node's outcome `new-request` (its `emit_outcome` output = the new request, verbatim) ends the run; the bridge immediately starts a new front-door run with `inputs.message` = that output and `inputs.earlier` = the old transcript, `inputs.continues` = old run id, and returns the **new** `session_id` (`continued_from` = old). Also used by `clarify`. | Re-classification with the new words gets the fast path back; "graph exits ⇒ conversation over" holds; this is exactly "loading an earlier conversation", done automatically. | Edges from converse nodes back to `front`: `front` reads `inputs.message`, which is still the *old* message — it would re-route the old request. |
| J11 | **Loading an earlier conversation** = `POST /sessions/{id}/continue {user_input}` → new front-door run with `inputs.earlier` = the old run's transcript (bounded to `settings.omni_earlier_chars`, newest kept). `/execute` with an **ended** run's id starts a **fresh** conversation (no seed) and says so (`new_conversation: true`). | The user's rule: once the graph exits the conversation is over; getting it back is an explicit load. | `/execute` on an ended id silently continues it: the conversation would never be over. |
| J12 | **Every run has the same three inputs**: `message` (str), `earlier` (str, `""` if none), `continues` (run id or `""`). | A declared input path that does not exist is a hard run error (`InvalidArgumentError` from `_resolve`), so optional inputs must always be present. | Omitting `earlier` on fresh runs: every node that declares it would fail. |
| J13 | **Asks are answered from the user's next message, interpreted by Jev.** A permission `Request` (physical tool) or an approval `Request` (human node) on the run stream (or a child run's) parks the conversation; the bridge returns its rendered question as the reply, with `conversation_state: "awaiting_approval"`. The next `/execute` message is classified by a Jev `choice` (`allow`/`deny` for permission; the request's own outcomes for approval) with threshold `0.75`; below it Alfred re-asks once ("Sorry — was that a yes?"), then denies. `POST /sessions/{id}/asks/{req_id}` answers directly (for UIs with buttons). | Keeps `alfred-ui` unchanged (it only sends text); Jev's `binary` calibrated poorly, so `choice` with named options. Physical actions still ask every time (D64) — this only makes answering them conversational. | Requiring a new UI button: blocks the feature on another repo. |
| J14 | **A pending ask is answered before anything else**; a message that clearly is not an answer still resolves the ask as `deny` (Jev picks `deny` for "no, actually turn on the kitchen one instead"), and Alfred says it did not do it. | One rule; never act on an ambiguous yes. | Holding the ask and also routing the new message: two concurrent intents in one conversation. |
| J15 | **AlfredCore is retired** (A9, last wave): `ai_server/core/`, `alfred_router/`, `intent_processor.py`, the SQLite `memory/` store and their tests are deleted; `/execute` and `/sessions` are served by the bridge only. Until A9 lands, `settings.alfred_backend` (`omni` default, `core` fallback) keeps the old path selectable. | migration.md Phase 5's exit criterion is "Alfred works with `ai_server/core/` deleted"; the user does not need backwards compatibility. Keeping it behind a flag until the e2e passes avoids a dark period. | Wrapping AlfredCore as a fallback node: a second brain with its own JSON protocol, which the Omni design exists to delete. |
| J16 | **Decide nodes in Alfred's graphs name `"provider": "typesafe", "model": "jev-latest"` in the file.** The daemon builds them with the kernel's credential chain (`TYPESAFE_API_KEY` env, else `~/.omni/providers/typesafe/config.yml` `api_key_file:`). The user creates that one config file (agents may not edit `~/.omni/`). Without it every decide node exits `error`, which every graph routes to an LLM fallback. | The kernel resolves keys this way for every vendor; a graph that degrades instead of failing is the right shape for a missing key. | The bridge pushing the key into the daemon's env: works only when *this* process spawned the daemon. Kept as a secondary path (A5) — not relied on. |
| J17 | **Agent nodes run as agent `alfred`**, whose `agent.yml` gains `decision: {provider: typesafe, model: jev-latest}` and `decide` in `tools.allow`. Front-door and subgraph agent nodes narrow `tools` to what they need (never `run_graph`/`create_graph`, except new-capability's designer). | One persona; the LLM can call `decide` for entity resolution; a node cannot start runs the walker does not bound. | A separate `alfred-node` agent: a second persona to keep in step. |
| J18 | **Re-adoption on restart:** at start-up the bridge lists live `client:graph` runs (`list_runs(module="client:graph")`) whose `definition_ref` is in its library and starts a `GraphHost.drive` for each; runs whose ref is unknown (graph edited since) are cancelled with reason `graph changed`. | `GraphHost` supports resume (its `_decided` logic); without a host a parked run would wait forever. | Leaving them: invisible stuck conversations. |
| J19 | **`/execute` waits for quiescence** (§7.4) up to `settings.omni_turn_timeout` (default 120 s) and returns whatever Alfred said so far with `conversation_state: "working"` if the run is still busy. | The UI is request/response; a long subgraph (new-capability) must not hold an HTTP request for minutes. | Streaming: needs UI work (5.5). |
| J20 | **The new-capability graph keeps its LLM `verify`.** Only renames for J8 (`installed` → `say-installed`, `declined` → `say-declined`). | Its verify is a judgement over code and test output that Jev cannot inspect; there is no clean evidence/verdict split. | Splitting it like troubleshoot: Jev would classify a report another LLM wrote — no gain. |
| J21 | **Cheap non-Omni bugs are fixed in wave 1** (A1): `plugins_dir` (0 plugins load), the Forge's removed `settings.alfred_router_model` and `"APPROVED" in feedback` substring bug, and the 3 piper tests skip when `piper` is not installed. | Each is a few lines; the plugins fix is a prerequisite for correct plugin tool grades (A2). | Leaving them: plugin tools never reach Omni. |
| J22 | **Alfred's real docs live in `docs/`**: `docs/architecture.md` (how Alfred is built), `docs/omni.md` (running on Omni: setup, bridge, conversations, secrets), `docs/graphs.md` (each graph node by node), plus this plan and its index. `ARCHITECTURE.md` and `CODEBASE_SUMMARY.md` get a "stale — see docs/" banner (A8). `CLAUDE.md` is git-ignored and local; it is rewritten last to point at `docs/` (A10). | One home for current docs; the root files describe AlfredRouter, which no longer exists. | Rewriting the root files in place: their history is useful and they are large. |
| J23 | **omni-kernel is consumed from its main checkout**: the coordinator switches `C:\Users\udayr\Documents\Projects\omni-kernel` to `feat/model-kinds` (A0) — the omni venv's editable install then serves the new kernel — and installs the same checkout editable into Alfred's venv (`pip install -e`). Any running `omnid` is restarted (protocol 7 → 8). | One kernel on the machine; no copies; nothing to configure per worktree. | Installing from the `ok-model-kinds` worktree: that worktree is temporary. |

---

## 3. Conventions (every task)

- **Worktree per task.** Branch `oj/A<n>-<slug>` from the *current tip* of `feat/omni-jev`;
  worktree `C:\Users\udayr\Documents\Projects\alfred-oj-A<n>`:
  `git -C C:\Users\udayr\Documents\Projects\alfred worktree add ..\alfred-oj-A<n> -b oj/A<n>-<slug> feat/omni-jev`.
  Commit on your branch (local). **Never push, never merge** — the coordinator merges.
- **Python.** Alfred's venv: `C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe`.
  Run everything **from your worktree root**; `ai_server` is not installed, so the cwd copy
  is the one imported. Check once:
  `…\python.exe -c "import ai_server; print(ai_server.__file__)"` must print your worktree.
- **omni in Alfred's venv** (after A0): `import omni` resolves to
  `C:\Users\udayr\Documents\Projects\omni-kernel\omni` on `feat/model-kinds`. Never
  `pip install` anything yourself. Code that imports `omni` does so **lazily** (inside the
  bridge, `ai_server/decisions.py`, and the HA server's resolve path) so the rest of Alfred
  imports without it; tests that need it start with `pytest.importorskip("omni")`.
- **The gate** — must pass before you report done; report the counts:

  ```
  C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
  ```

  Baseline at the plan commit: **93 passed, 3 failed, 6 skipped** (the 3 failures are
  `tests/test_synthesizer.py::TestSynthesizerSynthesize::*`, `piper` not installed). After
  A1 those three skip: **93 passed, 9 skipped, 0 failed** is the new floor. Every task adds
  tests; none may leave a failure.
- **Graph validation** (any task touching `.omni/graphs/`):
  `tests/test_graphs.py` (A3) validates every graph with `omni.graph.validate.validate`
  and lints J8; also runnable by hand:
  `C:\Users\udayr\Documents\Projects\omni-kernel\.venv\Scripts\omni.exe graph validate .omni\graphs\front-door.json`
  from the worktree root.
- **Live tests** are marked `@pytest.mark.live` and skip unless `ALFRED_LIVE=1`. Implementers
  do not run them against real Jev/Anthropic/HA; the coordinator does. Register the marker in
  `tests/conftest.py` (A1) — `pytest.ini`/`pyproject` do not exist here.
- **Secrets.** Never print, log, `cat`, `type`, echo or commit a key. The Jev key is
  `C:\Users\udayr\Documents\Projects\alfred\jev.env` (raw key, no `KEY=` prefix) and exists
  **only in the main checkout**, not in worktrees; code reads it only through
  `KeyFileCredential(settings.jev_key_file)`. Tests use a temp file with a fake key. Never
  commit `jev.env`, `.env`, `.claude/`, `AGENT_COORDINATION.md`, `TODO_BACKEND_FIXES.md`,
  `TODO_CHATUI.md`, `Refresh.md`, `Index_refresh.md`, `CLAUDE.md`, `.omni/mcp.local.json`.
  Do not edit `~/.omni/` or anything in `C:\Users\udayr\Documents\Projects\omni-kernel`.
- **Docs as you go.** Flip your `Planned (omni-jev, An)` blocks in `docs/*.md` to
  `As built (omni-jev, An)`, rewritten to match what you built; record any deviation from
  this plan in §12 with your task id. Do not rewrite other tasks' blocks.
- **Code style.** Match the surrounding Alfred code: type hints everywhere, `async` for I/O,
  `logger = logging.getLogger(__name__)`, Pydantic models with `extra="forbid"` for new API
  bodies, module docstrings that say *why*. Reference this plan's decisions (`J4`) in
  docstrings where a reader would otherwise wonder.
- **Report** (≤25 lines): branch, commit hash(es), gate counts, docs touched, §12 rows
  added, anything left undone.

---

## 4. Environment and setup

### 4.1 Coordinator steps (A0 — not an implementer task)

1. After omni-kernel's T10 is merged into `feat/model-kinds` and the kernel gate passes:
   `git -C C:\Users\udayr\Documents\Projects\omni-kernel checkout feat/model-kinds`
   (the main checkout must be clean first; it is on `integration/alfred-ready`, which
   `feat/model-kinds` contains).
2. Stop any running daemon: `C:\Users\udayr\Documents\Projects\omni-kernel\.venv\Scripts\omni.exe daemon stop`
   (or let the protocol mismatch force a respawn — a protocol-7 daemon refuses a
   protocol-8 client with `Unavailable`, which `open_client` treats as stale).
3. `C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m pip install -e C:\Users\udayr\Documents\Projects\omni-kernel`
   (core deps only: `httpx`, `PyYAML`, `structlog`; Alfred's venv already has `httpx`).
4. Verify: `…\alfred\.venv\Scripts\python.exe -c "import omni, omni.codec.client, omni.graph.host; from omni.kernel.api import PROTOCOL_VERSION; print(omni.__file__, PROTOCOL_VERSION)"` → main checkout path, `8`.
5. **User step (J16):** create `C:\Users\udayr\.omni\providers\typesafe\config.yml` with the
   single line `api_key_file: C:\Users\udayr\Documents\Projects\alfred\jev.env`. Until then
   decide nodes in the daemon exit `error` and every graph falls back to its LLM path.

### 4.2 How Alfred runs today (facts the plan relies on)

- Alfred's venv: Python 3.13.1 at `alfred\.venv`; `import omni` fails there today.
- The daemon `omnid` runs from the omni-kernel venv (`omni.exe`, `omnid.exe` console
  scripts). Clients find it through the discovery file under `~/.omni/run/`;
  `omni.cli.connect.open_client(spawn=True)` finds or starts one.
- Graph runs are `client:graph` runs (`omni.graph.policy.GRAPH_MODULE`): the kernel parks
  after every node until a host submits the next decision. `omni.graph.host.GraphHost`
  is that host; `start_graph_run(client, graph, inputs)` starts one with the graph's
  content hash as `definition_ref`.
- `omni.graph.tools.discover_library(cwd)` builds a `GraphLibrary` from `<cwd>/.omni/graphs`
  (plus the kernel presets).
- Node sessions are spawned by the daemon with a `SessionSpec` built from the node's
  `constraints` (`RunSupervisor._spec`): any `SessionSpec` field name is accepted
  (`cwd`, `mcp_enabled`, `mcp_config_path`, `on_ask`, `ask_timeout`, `provider`, `model`,
  `agent`, `tools`→`allowed_tools`…); an unknown key is an error. `on_ask` defaults to
  `deny` for node sessions; `"prompt"` relays the node's asks to the **run's** stream.
- MCP tools appear as `mcp__<server>__<tool>` (`mcp__home_assistant__turn_on`). Plugin
  tools are `mcp__alfred_plugins__<slug>_<verb>`; `tool_grades` keys in `mcp.json` are the
  per-server names (`<slug>_execute_command`).
- The `/execute` contract `alfred-ui` uses (`alfred-ui/src/services/api.js`):
  `POST /execute {user_input, session_id?, voice_mode}`; it reads `answer` (or `message`)
  and `session_id`, and `audio_base64` when `voice_mode`. `GET/POST /sessions`,
  `GET/DELETE /sessions/{id}`.

---

## 5. Shared: graphs

All graphs are `version: 1`. Node ids, outcome names and edges below are normative; prompt
wording may be improved by the implementer if the intent is kept. **J8 naming applies.**
A3 builds all of §5.

### 5.1 House constraints (injected, J6)

The bridge's `load_library()` (§7.2) applies, to every `agent` and `tool` node of every
graph it loads (front-door and the three subgraphs), `setdefault` of:

```python
{
  "cwd": str(REPO_ROOT),                         # absolute
  "mcp_enabled": True,
  "mcp_config_path": str(REPO_ROOT / ".omni" / "mcp.local.json"),
  "on_ask": "prompt",
  "ask_timeout": settings.omni_ask_timeout,      # seconds, default 300
  # only when set:
  "provider": settings.omni_node_provider,
  "model": settings.omni_node_model,
}
```

`decide` and `human` nodes are untouched. Graph files never contain these keys; the A3
graph test asserts it (a committed file with `cwd` or `mcp_config_path` fails).

### 5.2 `front-door.json` (new)

```json
{
  "version": 1,
  "name": "front-door",
  "description": "One conversation with Alfred: Jev routes the request; devices run without a chat model; agents that talk hold the floor.",
  "entry": "front",
  "terminal": ["say-act", "say-status", "device-help", "chat", "clarify",
               "troubleshoot", "routine", "new-capability"],
  "limits": { "max_nodes": 40, "cost_usd": 5.0, "wall_clock": "4h" },
  "nodes": [
    { "id": "front", "kind": "decide",
      "prompt": "A person is talking to their home assistant. What kind of request is this?",
      "inputs": ["inputs.message"],
      "outcomes": {
        "device":         "A direct command to operate or check one device right now: turn something on or off, toggle it, dim it, set it, or ask its state.",
        "troubleshoot":   "Something in the house is not working, misbehaving, offline or unresponsive and needs diagnosis.",
        "routine":        "Several devices should change together as a named scene or routine (bedtime, leaving, movie night).",
        "new-capability": "The person wants Alfred to learn to do something it cannot do yet, or to add a new integration or device type.",
        "chat":           "Conversation, a question, or anything that changes nothing in the house."
      },
      "threshold": 0.6,
      "constraints": { "provider": "typesafe", "model": "jev-latest", "timeout": "5s" } },

    { "id": "device-action", "kind": "decide",
      "prompt": "Which single action does the person want performed on the device?",
      "inputs": ["inputs.message"],
      "outcomes": {
        "turn-on":  "Switch it on, open it, start it.",
        "turn-off": "Switch it off, close it, stop it.",
        "toggle":   "Flip it to the opposite of whatever it is now.",
        "status":   "Only report its current state; change nothing.",
        "other":    "Anything else: a level, a colour, a temperature, a mode, several devices, or unclear."
      },
      "threshold": 0.6,
      "constraints": { "provider": "typesafe", "model": "jev-latest", "timeout": "5s" } },

    { "id": "resolve", "kind": "tool",
      "capability": "mcp__home_assistant__resolve_entity",
      "args": { "request": "{{ inputs.message }}", "action": "{{ outputs.device-action.choice }}" },
      "outcomes": {
        "resolved":  "Exactly one device matched with confidence.",
        "ambiguous": "Several devices could match, or confidence was low.",
        "none":      "No device matched."
      } },

    { "id": "resolve-read", "kind": "tool",
      "capability": "mcp__home_assistant__resolve_entity",
      "args": { "request": "{{ inputs.message }}", "action": "status" },
      "outcomes": {
        "resolved":  "Exactly one device matched with confidence.",
        "ambiguous": "Several devices could match, or confidence was low.",
        "none":      "No device matched."
      } },

    { "id": "say-act", "kind": "tool",
      "capability": "mcp__home_assistant__apply",
      "args": { "action": "{{ outputs.device-action.choice }}",
                "target": "{{ outputs.resolve }}",
                "request": "{{ inputs.message }}" } },

    { "id": "say-status", "kind": "tool",
      "capability": "mcp__home_assistant__describe",
      "args": { "target": "{{ outputs.resolve-read }}" } },

    { "id": "device-help", "kind": "agent", "agent": "alfred",
      "prompt": "The person asked for something about a device that needs judgement: inputs.message. Work out which device (use list_entities, and the decide tool to match a description to an entity), do what they asked with the Home Assistant tools, and tell them what happened in one or two sentences. Ask them when you genuinely cannot tell which device. If what they say next is a different request entirely, call emit_outcome with 'new-request' and put their new request, in their words, as the output. When they are done, call emit_outcome with 'done'.",
      "inputs": ["inputs.message", "inputs.earlier"],
      "outcomes": {
        "done":        "The device request is handled and the person has nothing more on it.",
        "new-request": "The person moved on to a different request; output is that request, verbatim."
      },
      "converse": true, "floor_timeout": "15m",
      "constraints": { "tools": ["mcp", "decide", "clock", "remember"] } },

    { "id": "chat", "kind": "agent", "agent": "alfred",
      "prompt": "Talk with the person. inputs.earlier, if not empty, is an earlier conversation they chose to continue. If they ask you to do something in the house, call emit_outcome with 'new-request' and put the request, in their words, as the output — do not do it yourself. When the conversation is finished, call emit_outcome with 'done'.",
      "inputs": ["inputs.message", "inputs.earlier"],
      "outcomes": {
        "done":        "The conversation is finished.",
        "new-request": "The person asked for something to be done; output is that request, verbatim."
      },
      "converse": true, "floor_timeout": "30m",
      "constraints": { "tools": ["clock", "remember"] } },

    { "id": "clarify", "kind": "agent", "agent": "alfred",
      "prompt": "It was not clear what the person wants (inputs.message). Ask one short question to find out. As soon as you know, call emit_outcome with 'new-request' and put their request, restated clearly in their words, as the output. If it turns out they only wanted to chat, chat, and call emit_outcome with 'done' when finished.",
      "inputs": ["inputs.message", "inputs.earlier"],
      "outcomes": {
        "new-request": "The request is now clear; output is the clear request.",
        "done":        "It was conversation and it is finished."
      },
      "converse": true, "floor_timeout": "10m",
      "constraints": { "tools": ["clock"] } },

    { "id": "troubleshoot", "kind": "graph", "graph": "device-troubleshoot",
      "inputs": { "report": "{{ inputs.message }}", "earlier": "{{ inputs.earlier }}" },
      "outcomes": ["done", "new-request"] },

    { "id": "routine", "kind": "graph", "graph": "home-routine",
      "inputs": { "routine": "{{ inputs.message }}", "earlier": "{{ inputs.earlier }}" },
      "outcomes": ["done"] },

    { "id": "new-capability", "kind": "graph", "graph": "new-capability",
      "inputs": { "request": "{{ inputs.message }}" },
      "outcomes": ["done"] }
  ],
  "edges": [
    { "from": "front", "to": "device-action",  "on": "device" },
    { "from": "front", "to": "troubleshoot",   "on": "troubleshoot" },
    { "from": "front", "to": "routine",        "on": "routine" },
    { "from": "front", "to": "new-capability", "on": "new-capability" },
    { "from": "front", "to": "chat",           "on": "chat" },
    { "from": "front", "to": "clarify",        "on": "unsure" },
    { "from": "front", "to": "clarify",        "on": "error" },
    { "from": "front", "to": "clarify",        "on": "timeout" },

    { "from": "device-action", "to": "resolve",      "on": "turn-on" },
    { "from": "device-action", "to": "resolve",      "on": "turn-off" },
    { "from": "device-action", "to": "resolve",      "on": "toggle" },
    { "from": "device-action", "to": "resolve-read", "on": "status" },
    { "from": "device-action", "to": "device-help",  "on": "other" },
    { "from": "device-action", "to": "device-help",  "on": "unsure" },
    { "from": "device-action", "to": "device-help",  "on": "error" },
    { "from": "device-action", "to": "device-help",  "on": "timeout" },

    { "from": "resolve", "to": "say-act",     "on": "resolved" },
    { "from": "resolve", "to": "device-help", "on": "ambiguous" },
    { "from": "resolve", "to": "device-help", "on": "none" },
    { "from": "resolve", "to": "device-help", "on": "error" },

    { "from": "resolve-read", "to": "say-status",  "on": "resolved" },
    { "from": "resolve-read", "to": "device-help", "on": "ambiguous" },
    { "from": "resolve-read", "to": "device-help", "on": "none" },
    { "from": "resolve-read", "to": "device-help", "on": "error" }
  ]
}
```

Notes for A3:
- Terminal nodes have no outgoing edges (graph-spec §7.2). `say-act` / `say-status` are
  terminal tool nodes: an `error` exit (HA failed, permission denied) ends the run; the
  bridge turns a speaking tool's error into "I couldn't do that — <reason>." (§7.5).
- A `graph` node's `outcomes` must be the subgraph's terminal outcomes; validate against
  the library (`validate(graph, library=…)`) and adjust declared outcomes to what the
  validator requires. Record any change in §12.
- `device-action` and `resolve` paths use hyphenated node ids inside placeholders; the
  kernel's placeholder regex accepts `-` (`blackboard._PLACEHOLDER`).
- `{{ outputs.resolve }}` as a *whole* string keeps its type (a JSON string) — that is the
  point of J4.

### 5.3 `device-troubleshoot.json` (rewritten)

Inputs change from `device`/`symptom` to `report` (the user's words) and `earlier`. The
`home-graphs` skill text that documents the old inputs is updated in A3.

```json
{
  "version": 1,
  "name": "device-troubleshoot",
  "description": "Something in the house is not working: find out what with the person, gather evidence, let Jev triage it, attempt one bounded recovery, confirm it from the source, and tell the person.",
  "entry": "intake",
  "terminal": ["say-report", "say-escalate", "say-bye"],
  "limits": { "max_nodes": 30, "cost_usd": 1.5, "wall_clock": "45m" },
  "nodes": [
    { "id": "intake", "kind": "agent", "agent": "alfred",
      "prompt": "The person reported a problem: inputs.report (inputs.earlier, if not empty, is an earlier conversation they continued). Find out which device and what exactly is wrong — read entity state with the tools and use the decide tool to match their words to an entity; ask them only what you cannot read. When you know the device (entity_id and name) and the symptom, call emit_outcome with 'identified' and put both, plainly, as the output. If they move on to something else entirely, call emit_outcome with 'new-request' with that request as the output. If they want to stop, 'gave-up'.",
      "inputs": ["inputs.report", "inputs.earlier"],
      "outcomes": {
        "identified":  "Device and symptom are known; output names both.",
        "new-request": "The person moved on; output is the new request, verbatim.",
        "gave-up":     "The person does not want to continue."
      },
      "converse": true, "floor_timeout": "15m",
      "constraints": { "tools": ["mcp", "decide", "clock"] } },

    { "id": "inspect", "kind": "agent", "agent": "alfred",
      "prompt": "Gather evidence about the device and symptom in outputs.intake. Read the entity's current state, its last-changed and last-updated times, and whether the integration behind it is reachable. Do not change anything. Write a short factual report: what you read, and which of these it looks like — unreachable, reachable but reporting an unexpected state, or fine (the request was ambiguous). Then call emit_outcome with 'done' and the report as the output.",
      "inputs": ["outputs.intake"],
      "outcomes": ["done"],
      "constraints": { "tools": ["mcp", "clock"] } },

    { "id": "triage", "kind": "decide",
      "prompt": "Given the problem and the evidence, what should happen next?",
      "inputs": ["outputs.intake", "outputs.inspect"],
      "outcomes": {
        "recoverable": "There is one specific remote action worth trying (reload an integration, re-send a command, toggle it).",
        "healthy":     "Nothing is actually wrong; the device is doing what it should.",
        "needs-human": "The fix is physical: a battery, a breaker, an unplugged bridge, a device out of range."
      },
      "threshold": 0.7,
      "constraints": { "provider": "typesafe", "model": "jev-latest", "timeout": "5s" } },

    { "id": "diagnose-llm", "kind": "agent", "agent": "alfred",
      "prompt": "Decide from the problem in outputs.intake and the evidence in outputs.inspect: emit 'recoverable' if there is a specific action worth trying, 'healthy' if nothing is actually wrong, or 'needs-human' if the fix is physical. Say why in one sentence as the output.",
      "inputs": ["outputs.intake", "outputs.inspect"],
      "outcomes": ["recoverable", "healthy", "needs-human"],
      "constraints": { "tools": ["mcp", "clock"] } },

    { "id": "recover", "kind": "agent", "agent": "alfred",
      "prompt": "Attempt exactly one recovery for the device in outputs.intake, guided by the evidence in outputs.inspect. One attempt. If the action is physical it will ask, as it should. Emit 'recovered' or 'still-broken', and put what the call actually returned as the output.",
      "inputs": ["outputs.intake", "outputs.inspect"],
      "outcomes": ["recovered", "still-broken"],
      "constraints": { "tools": ["mcp", "clock"] } },

    { "id": "read-back", "kind": "agent", "agent": "alfred",
      "prompt": "Read the device in outputs.intake back from the source now. Report its state and timestamps factually. A call that returned success is not evidence; the read-back is. Emit 'done' with the report as the output.",
      "inputs": ["outputs.intake", "outputs.recover"],
      "outcomes": ["done"],
      "constraints": { "tools": ["mcp", "clock"] } },

    { "id": "fixed", "kind": "decide",
      "prompt": "Is the original symptom genuinely gone, according to the read-back?",
      "inputs": ["outputs.intake", "outputs.read-back"],
      "outcomes": {
        "fixed":     "The read-back shows the device now behaves as the person wanted.",
        "not-fixed": "The read-back still shows the problem, or does not show it gone."
      },
      "threshold": 0.7,
      "constraints": { "provider": "typesafe", "model": "jev-latest", "timeout": "5s" } },

    { "id": "confirm-llm", "kind": "agent", "agent": "alfred",
      "prompt": "Compare the symptom in outputs.intake with the read-back in outputs.read-back. Emit 'fixed' only if the read-back shows the symptom gone; otherwise 'not-fixed'.",
      "inputs": ["outputs.intake", "outputs.read-back"],
      "outcomes": ["fixed", "not-fixed"],
      "constraints": { "tools": ["clock"] } },

    { "id": "say-report", "kind": "agent", "agent": "alfred",
      "prompt": "Tell the person what was wrong and what you did about it, briefly and in your own voice. If nothing was wrong, say what the device is actually doing and why it may have looked broken. Use run_context to read whichever of outputs.recover and outputs.read-back exist.",
      "inputs": ["outputs.intake", "outputs.inspect"],
      "readable": ["outputs.*"],
      "outcomes": ["done"],
      "constraints": { "tools": ["clock"] } },

    { "id": "say-escalate", "kind": "agent", "agent": "alfred",
      "prompt": "You could not fix this from here. Give the person the shortest specific thing they can do about it — which device, where, what to check. Say plainly what you tried and what it returned (use run_context to read outputs.recover and outputs.read-back if they exist).",
      "inputs": ["outputs.intake", "outputs.inspect"],
      "readable": ["outputs.*"],
      "outcomes": ["done"],
      "constraints": { "tools": ["clock"] } },

    { "id": "say-bye", "kind": "agent", "agent": "alfred",
      "prompt": "Read outputs.intake. If the person moved on to a new request, call emit_outcome with 'new-request' and repeat that request verbatim as the output, and say nothing to the person. Otherwise say a one-line goodbye and call emit_outcome with 'done'.",
      "inputs": ["outputs.intake"],
      "outcomes": {
        "done":        "The person stopped; a goodbye was said.",
        "new-request": "The person moved on; output is the new request, verbatim."
      },
      "constraints": { "tools": ["clock"] } }
  ],
  "edges": [
    { "from": "intake", "to": "inspect",  "on": "identified" },
    { "from": "intake", "to": "say-bye",  "on": "new-request" },
    { "from": "intake", "to": "say-bye",  "on": "gave-up" },
    { "from": "inspect", "to": "triage",      "on": "done" },
    { "from": "triage", "to": "recover",      "on": "recoverable" },
    { "from": "triage", "to": "say-report",   "on": "healthy" },
    { "from": "triage", "to": "say-escalate", "on": "needs-human" },
    { "from": "triage", "to": "diagnose-llm", "on": "unsure" },
    { "from": "triage", "to": "diagnose-llm", "on": "error" },
    { "from": "triage", "to": "diagnose-llm", "on": "timeout" },
    { "from": "diagnose-llm", "to": "recover",      "on": "recoverable" },
    { "from": "diagnose-llm", "to": "say-report",   "on": "healthy" },
    { "from": "diagnose-llm", "to": "say-escalate", "on": "needs-human" },
    { "from": "recover", "to": "read-back",    "on": "recovered" },
    { "from": "recover", "to": "say-escalate", "on": "still-broken" },
    { "from": "read-back", "to": "fixed",      "on": "done" },
    { "from": "fixed", "to": "say-report",     "on": "fixed" },
    { "from": "fixed", "to": "recover",        "on": "not-fixed", "max_retries": 1 },
    { "from": "fixed", "to": "confirm-llm",    "on": "unsure" },
    { "from": "fixed", "to": "confirm-llm",    "on": "error" },
    { "from": "fixed", "to": "confirm-llm",    "on": "timeout" },
    { "from": "confirm-llm", "to": "say-report", "on": "fixed" },
    { "from": "confirm-llm", "to": "recover",    "on": "not-fixed", "max_retries": 1 }
  ]
}
```

**Why `say-bye` exists (decided).** A terminal node has no outgoing edges (graph-spec
§7.2), so a node cannot both branch (`intake` → `inspect` on `identified`) and end the run
(on `new-request`/`gave-up`). For front-door to hand off (J10), the subgraph's *terminal*
outcome must be `new-request`, which the run returns through front-door's `troubleshoot`
graph node. `say-bye` is that terminal: one short LLM turn on a rare path. It is a speaker
by name, so the bridge must **not** speak its text when its outcome is `new-request`
(§7.5 rule 4). Rejected: splitting the graph in two (`intake` alone, then a repair graph)
— it only moves the same branching problem into front-door; a tool node as the exit — a
tool cannot return an outcome that depends on what `intake` said.

The front-door `troubleshoot` node's `outcomes` are then `["done", "new-request"]` (from
`say-report`/`say-escalate`/`say-bye`).

### 5.4 `home-routine.json` (verify split; J8 renames)

Changes from the current file (keep every other field as it is today):
- Inputs: `routine` (unchanged) plus `earlier` (passed by front-door; `plan` declares it).
- `plan`: inputs `["inputs.routine", "inputs.earlier"]`; constraints `tools: ["mcp", "decide", "clock"]`.
- `apply`: constraints `tools: ["mcp", "clock"]`.
- Replace `verify` with:
  - `read-back` (agent, outcomes `["done"]`, inputs `["outputs.plan", "outputs.apply"]`,
    prompt: read every device in the plan back from the source and report each one's
    intended vs actual state; emit `done` with the report), tools `["mcp", "clock"]`;
  - `settled` (decide, inputs `["outputs.plan", "outputs.read-back"]`, outcomes
    `{"settled": "Every device in the plan now reads back as intended.", "drifted": "At least one device did not take, or its state is unknown."}`,
    threshold `0.7`, typesafe/jev-latest, timeout `5s`);
  - `verify-llm` (agent, the old `verify` prompt and outcomes `["settled", "drifted"]`,
    inputs `["outputs.plan", "outputs.apply", "outputs.read-back"]`).
  - Edges: `apply --applied|partial--> read-back`, `read-back --done--> settled`,
    `settled --settled|drifted--> say-report`, `settled --unsure|error|timeout--> verify-llm`,
    `verify-llm --settled|drifted--> say-report`.
- Rename `report` → `say-report` (inputs `["outputs.plan", "outputs.apply"]` plus
  `"readable": ["outputs.*"]` so it can read whichever of `read-back`/`verify-llm`/`settled`
  ran) and `cancelled` → `say-cancelled`. `terminal: ["say-report", "say-cancelled"]`.
  Both declare `"outcomes": ["done"]`.
- `confirm` (human) is unchanged: its `Request` is surfaced by the bridge (§7.6).

### 5.5 `new-capability.json` (J20: renames only)

- `installed` → `say-installed`, `declined` → `say-declined`; `terminal` updated; both
  `"outcomes": ["done"]`. Every edge that pointed at them is renamed.
- Agent nodes get constraints `tools` only where the file has none today:
  `build` and `verify` keep the agents' own allow-lists (no change).
- Nothing else changes. Its `approve` and `review` human nodes are surfaced by the bridge.

### 5.6 `agent.yml` and skill changes (A3)

- `.omni/alfred/agent.yml`: add
  ```yaml
  decision:
    provider: typesafe
    model: jev-latest
  ```
  and `decide` and `run_context` to `tools.allow` (`run_context` is only granted to nodes
  that declare `readable`; listing it in the allow-list keeps a narrowed node from losing
  it — the kernel widens for it anyway, kernel §10 T9).
- `.omni/skills/home-graphs/SKILL.md`: document `front-door`, the new
  `device-troubleshoot` inputs (`report`, `earlier`), J8's speaker rule and J10's
  `new-request` convention, so an LLM writing a graph with `create_graph` follows them.

---

## 6. Shared: Home Assistant MCP tools and Jev in Alfred

### 6.1 `ai_server/decisions.py` (A2, new)

```python
"""Alfred's one door to a decision model (J5).

Lazily imports omni-kernel. Used by the HA MCP server (resolve_entity) and by the
bridge's ask interpreter (§7.6). Never logs `state` above DEBUG.
"""
def get_decision_model() -> "DecisionModel | None":
    """A cached TypeSafeDecision(model=settings.decision_model) whose credentials are
    KeyFileCredential(settings.jev_key_file), or None when the key file does not exist,
    omni is not importable, or settings.decision_provider != "typesafe"
    (then: None — callers degrade)."""

async def choose(question: str, options: Mapping[str, str], state: Mapping[str, Any],
                 *, threshold: float) -> tuple[str | None, float]:
    """One Jev `choice` question. Returns (answer, confidence); answer is None when the
    model is unavailable, raised, or confidence < threshold. Never raises."""

def close() -> None: ...   # closes the cached model (called on shutdown)
```

Implementation notes: build the model with `TypeSafeDecision(model=…, credentials=…)` —
read its constructor in `omni/driver/provider/typesafe.py` (or wherever `TypeSafeDecision`
is exported from `omni.driver.provider`); questions are
`omni.driver.provider.Question(name="q", type="choice", instructions=question, criteria=options)`;
the result is `result.decisions["q"]` with `.choice` and `.confidence`. Catch every
`Exception` from `decide` and return `(None, 0.0)` with a WARNING naming only the exception
class.

### 6.2 New HA MCP tools (A2, in `ai_server/mcp/home_assistant_server.py`)

| Tool | Grade (`mcp.json`) | Args (JSON schema) | Result |
|---|---|---|---|
| `resolve_entity` | `read` | `request: string` (the person's words), `action: string` (one of `turn-on`, `turn-off`, `toggle`, `status`) | a JSON **object** `{"outcome": "resolved"\|"ambiguous"\|"none", "output": {...}}` (the kernel's tool-outcome routing, `route_tool_result`) |
| `apply` | `physical` (server default) | `action: string` (`turn-on`\|`turn-off`\|`toggle`), `target: string` (the JSON string `resolve_entity` produced as output), `request: string` | plain text, one sentence: `"Bedroom Lamp is on."`; error results are `is_error=True` with a short reason |
| `describe` | `read` | `target: string` (as above) | plain text, one sentence: `"Bedroom Lamp is off (last changed 14:02)."` |

`resolve_entity` behaviour:
1. `discover_devices()`; restrict to domains that support `action` (`status`: all
   `_KNOWN_DOMAINS` plus `sensor`, `binary_sensor`, `lock`; others: `_KNOWN_DOMAINS`).
2. No candidates → `{"outcome": "none", "output": {"reason": "no matching devices"}}`.
   Exactly one candidate → resolved without calling Jev.
3. Otherwise `decisions.choose("Which device does the person mean?", options, state={"request": request}, threshold=settings.resolve_threshold)`
   with `options = {entity_id: f"{friendly name} — {domain}, currently {state}"}`.
   More than `settings.resolve_max_candidates` (default 200) candidates → keep the first
   200 by name similarity is **not** attempted; return `ambiguous` with reason
   `too many devices` (Jev's context is ~32k tokens; a house rarely exceeds this).
4. Answer → `{"outcome": "resolved", "output": {"entity_id", "entity_name", "domain", "confidence"}}`;
   below threshold or model unavailable → `{"outcome": "ambiguous", "output": {"candidates": [top 5 entity_ids by probability if known, else first 5]}}`.

`apply` parses `target` (`json.loads`; must be an object with `entity_id` and
`entity_name`); maps `turn-on`/`turn-off`/`toggle` to the existing `_turn_on`/`_turn_off`/
`_toggle` logic (`Command(action=Action.TURN_ON, target=entity_id)` etc.); returns the
sentence. A bad `target` or `action` is an error result naming the problem. **It must carry
`entity_name` in its input schema description** so the permission prompt shows the device:
the bridge renders the prompt from `args.target`'s `entity_name` (§7.6).

`describe` parses `target` the same way and renders `get_device_info`'s state and
`last_changed` (if present in attributes) as one sentence.

### 6.3 `mcp.json` (A2)

```jsonc
"home_assistant": {
  "command": "python", "args": ["-m", "ai_server.mcp.home_assistant_server"],
  "cwd": "..",
  "grade": "physical",
  "tool_grades": { "get_state": "read", "list_entities": "read", "health_check": "read",
                   "resolve_entity": "read", "describe": "read" }
},
"alfred_plugins": {
  "command": "python", "args": ["-m", "ai_server.mcp.plugins_server"],
  "cwd": "..",
  "tool_grades": { "<slug>_get_device_info": "read", "<slug>_discover_devices": "read",
                   "<slug>_health_check": "read", "<slug>_execute_command": "physical", ... }
}
```

Fill `<slug>` for **every** plugin in `ai_server/plugins/` (after A1's `plugins_dir` fix the
server loads them; compute the slugs with `plugins_server._slug(integration.name)` and
list them literally). Keep the `_comment` block, updated. A test asserts that every tool
`plugins_server.build_tools(...)` exposes has an entry.

### 6.4 Mock Home Assistant (A2)

Move `smoke_test.py`'s `_MOCK_STATES` and `_MockHaHandler` into
`ai_server/mcp/mock_ha.py` (`start_mock_ha() -> (url, server)`; add a few more entities: a
second lamp `light.living_room_lamp`, `switch.coffee_maker`, `binary_sensor.hallway_motion`)
so unit tests, `smoke_test.py` and the A7 live smoke share it. Add `POST /api/services/...`
handling that flips `_MOCK_STATES` so `apply` is observable.

---

## 7. Shared: the bridge (`ai_server/omni_bridge/`)

### 7.1 Layout

| Module | Task | What |
|---|---|---|
| `__init__.py` | A4 | exports `AlfredOmni`, `ConversationReply`, `ConversationState` |
| `models.py` | A4 | the data shapes of §7.3 (Pydantic, `extra="forbid"`) |
| `library.py` | A4 | `load_library(repo_root, settings) -> tuple[GraphLibrary, dict[str, Graph]]` with J6 injection; `write_local_mcp_config(repo_root) -> Path` (J7) |
| `speakers.py` | A4 | J8 rule + reply extraction from envelopes (§7.5) |
| `transcript.py` | A4 | transcript from a run's envelopes and its node sessions' envelopes (§7.7) |
| `asks.py` | A4 | rendering a `Request` as a question; interpreting the reply (§7.6), with the decision function **injected** |
| `quiescence.py` | A4 | the "is it Alfred's turn to speak?" rule over a folded run (§7.4) |
| `client.py` | A5 | `AlfredOmni`: connection, `GraphHost` tasks, stream readers, the conversation API (§7.2) |

`speakers`, `transcript`, `asks`, `quiescence` and `library` are **pure** (no I/O, no
`await`) and take kernel event objects (`omni.kernel.events.*`, wrapped in
`omni.kernel.journal` envelopes — use whatever type `NativeClient` streams yield) as input.
That is what makes them unit-testable with hand-built events (A4) while `client.py` stays a
thin I/O loop (A5).

### 7.2 `AlfredOmni` API (A5 builds; A6 codes `main.py` against exactly this)

```python
class AlfredOmni:
    def __init__(self, repo_root: Path, settings: Settings,
                 *, decide: Callable[..., Awaitable[tuple[str | None, float]]] = decisions.choose,
                 client_factory: Callable[[], Awaitable[Any]] | None = None) -> None: ...
    async def start(self) -> None:
        """write_local_mcp_config; load_library; open_client(spawn=True); re-adopt live runs (J18)."""
    async def close(self) -> None: ...
    async def healthy(self) -> bool: ...                       # connected and daemon answers
    async def say(self, text: str, conversation_id: str | None) -> ConversationReply:
        """The /execute path. Routes per §7.8: pending ask → answer it; live floor → prompt;
        unknown/None id → new conversation; ended id → new conversation (J11)."""
    async def continue_from(self, conversation_id: str, text: str) -> ConversationReply:   # J11
    async def answer_ask(self, conversation_id: str, req_id: str, decision: str) -> ConversationReply:
    async def list_conversations(self, *, include_hidden: bool = False) -> list[ConversationSummary]:
    async def transcript(self, conversation_id: str) -> Transcript:
    async def forget(self, conversation_id: str) -> bool:      # cancel if live + hide (§8)
```

All methods raise `ConversationNotFound` (subclass of `LookupError`) for an unknown id
where the method requires one, and `BridgeUnavailable` (subclass of `RuntimeError`) when the
daemon cannot be reached; `main.py` maps them to 404 / 503.

### 7.3 Data shapes (`models.py`)

```python
ConversationState = Literal["awaiting_user", "awaiting_approval", "working", "ended"]

class PendingAsk(BaseModel):          # model_config = ConfigDict(extra="forbid")
    req_id: str
    stream_id: str                    # run (or child run) stream that carries the Request
    kind: Literal["permission", "approval", "elicit"]
    question: str                     # rendered, what Alfred says
    options: list[str]                # permission: ["allow", "deny"]; approval: the request's outcomes
    reasks: int = 0

class ConversationReply(BaseModel):
    conversation_id: str              # the run id (J1); NEW id after a handoff or a fresh start
    answer: str                       # what Alfred says now ("" if nothing yet)
    state: ConversationState
    pending: PendingAsk | None = None
    new_conversation: bool = False    # this reply started a run
    continued_from: str | None = None # J10/J11 seed source

class ConversationSummary(BaseModel):
    conversation_id: str
    title: str                        # first 60 chars of inputs.message
    started_at: str                   # ISO 8601
    last_active: str
    state: ConversationState
    continued_from: str | None = None

class TranscriptEntry(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    node: str | None = None           # graph node that spoke (assistant only)
    at: str | None = None

class Transcript(BaseModel):
    conversation: ConversationSummary
    entries: list[TranscriptEntry]
```

### 7.4 Quiescence — when `say()` returns

After submitting work (start / prompt / respond), `say()` reads the run's stream (and any
child run's and node sessions' streams it is attached to) until one of:
1. **Ended:** the front-door run's `RunEnded` arrived → `state="ended"` (then J10 handoff
   if the terminal node's outcome is `new-request`).
2. **Awaiting approval:** an unanswered `Request` on the run's or a child run's stream →
   `state="awaiting_approval"`, `pending` set.
3. **Awaiting user:** a converse node holds the floor and its session's latest turn has
   ended — i.e. the fold of the run that owns the floor (`omni.kernel.runs.fold.fold_run`)
   has `floor` set to `(node_id, session_id, …)` and the last `TurnEnded` on that session's
   stream is newer than the last `TurnStarted` → `state="awaiting_user"`.
   (The first time is announced by `FloorHeld`; later turns are not — kernel §10 T7 — so
   the rule is the fold plus the session's last turn, never `FloorHeld` alone.)
4. **Timeout:** `settings.omni_turn_timeout` elapsed → `state="working"`.

`quiescence.state_of(run_folds, session_turns) -> ConversationState | None` is the pure
part (A4); `None` = keep reading.

### 7.5 What Alfred says (speakers)

Collected from the moment `say()` submitted work until quiescence, in journal order:
1. A node is a **speaker** iff `node.converse` is true or `node.id.startswith("say-")`
   (J8), looked up in the graph the node belongs to (front-door or the subgraph whose child
   run entered it — the bridge maps child run → graph by `definition_ref`).
2. **Agent speaker:** the text of every `assistant` message block (`BlockEnded` of a text
   block whose `MessageStarted.role == "assistant"`) on its session's stream, from turns
   that started after the submission. The kernel's seed prompt and the user's own prompts
   are `user` messages and are never spoken.
3. **Tool speaker:** the `CallEnded.content` of its call (on the node session's stream);
   if the outcome is `err`, say `f"I couldn't do that — {content}"` (trim to 200 chars).
4. **Suppressed:** a terminal speaker whose node outcome is `new-request` (§5.3's
   `say-bye`, any converse node handing off) — its text is not spoken; the handoff's first
   reply is.
5. Pieces are joined with a blank line. If nothing was said and the state is
   `awaiting_approval`, the answer is the pending ask's `question`; if both exist, the ask
   question is appended.

### 7.6 Asks (permission and approval)

Rendering (`asks.render(request, graph_lookup) -> PendingAsk`):
- **Permission** (`RequestKind.PERMISSION`): read the tool name and args from
  `request.payload` (read the kernel's permission `Request` payload shape in
  session-api.md before coding — search for `PERMISSION`). If the tool is
  `mcp__home_assistant__apply`, parse `args["target"]` and say
  `f"Shall I {verb} {entity_name}?"` (`turn-on` → "turn on", …). For any other tool with an
  `entity_name` arg: `f"Shall I run {short_tool_name} on {entity_name}?"`. Otherwise
  `f"Alfred wants to use {short_tool_name}. Allow it?"`. Options `["allow", "deny"]`.
  (The kernel's own decision words for permission requests are whatever its `respond`
  accepts — map `allow`/`deny` onto them; record the mapping in §12.)
- **Approval** (`RequestKind.APPROVAL`, a `human` node): `request.payload`'s `prompt` plus
  a short rendering of its `show` material (each shown path's value, trimmed to 400 chars),
  then `"(" + " / ".join(options) + ")"`. Options are the request's outcomes.
- **Elicit:** render the prompt; options `[]`; the reply text is passed as `data`.

Interpreting the reply (`asks.interpret(pending, text, decide) -> str | None`):
- Permission: `decide("Does this reply give permission?", {"allow": "yes, go ahead, do it, sure, please", "deny": "no, stop, don't, cancel, or a different request instead"}, {"question": pending.question, "reply": text}, threshold=0.75)`.
- Approval: same, with the request's outcomes and their descriptions if the node has them
  (`Node.outcome_descriptions`), else the outcome names as their own descriptions.
- `None` (unsure/unavailable) → first time: re-ask (`reasks += 1`, answer
  `"Sorry — was that a yes or a no?"` / the approval options); second time: answer `deny`
  (or the approval's `on_timeout` outcome if the node has one, else its last outcome) and
  tell the person nothing was done.
- J14: a pending ask is resolved before anything else in the same message.

### 7.7 Transcript and the `earlier` seed

`transcript.build(run_envelopes, child_runs, session_streams, graphs) -> list[TranscriptEntry]`:
1. First entry: `user`, `inputs.message` of `RunStarted`.
2. Then, in journal order across the run and its child runs: for each speaker node, its
   spoken text (§7.5 rules 2–4) as `assistant` entries, and for converse nodes every
   `user` message on its session **after the first** (the first is the kernel's seed) as
   `user` entries.
3. Answers to asks are `user` entries (from the bridge's own record is **not** possible
   after a restart — use the `Response` events on the run streams: `Response.data` carries
   nothing human-readable, so render as `"(approved: <decision>)"`).

`earlier` text for a seed: `"\n".join(f"{e.role}: {e.text}" for e in entries)`, keeping the
**newest** `settings.omni_earlier_chars` (default 6000) characters.

### 7.8 Routing in `say(text, conversation_id)`

```
id is None or unknown or hidden        → start new run (inputs: message=text, earlier="", continues="")
id is a live run with a pending ask    → interpret (§7.6) → respond → wait (§7.4)
id is a live run with a held floor     → client.prompt(floor_session_id, text) → wait
id is a live run, still working        → reply state="working", answer "I'm still on it." (do not queue)
id is an ended run                     → start new run (fresh, J11) → new_conversation=True
```

After any wait that ends `ended` with terminal outcome `new-request`: read the terminal
node's output (its `NodeExited.output_ref` → the referenced envelope's content; for an
`emit_outcome` call that is the `output` argument), build `earlier` from the ended run's
transcript, start a new run (`message`=output, `earlier`, `continues`=old id), wait, and
return that reply with `continued_from`=old id, `new_conversation=True`. At most **one**
automatic handoff per `say()` (a second `new-request` simply ends with
`state="ended"`).

`GraphHost` per run: `GraphHost(client, GraphPolicy(library=…), on_request=self._on_request)`
driven in an `asyncio.Task`; child runs are adopted by the host itself. **Every stream the
bridge attaches must be read continuously** (NativeClient stalls the whole connection on a
full stream queue — `omni/codec/client.py` docstring item 2): one reader task per attached
stream, feeding a per-conversation `asyncio.Queue`.

### 7.9 Hidden conversations

`forget()` cancels a live run (`cancel_run(run_id, "forgotten")`) and adds its id to
`<settings.data_dir>/hidden_conversations.json` (a JSON list; created on demand; the
directory is git-ignored). Listing skips hidden ids. The journal is append-only and is
never edited.

---

## 8. Shared: HTTP API (A6)

`main.py` keeps every existing route and shape the UI uses, served by `AlfredOmni` when
`settings.alfred_backend == "omni"` (default):

| Route | Request | Response |
|---|---|---|
| `POST /execute` | `{user_input, session_id?, voice_mode}` (unchanged) | `{"intent": "conversation", "answer", "message": <same as answer>, "session_id": reply.conversation_id, "conversation_state": reply.state, "pending": reply.pending \| null, "new_conversation", "continued_from", "audio_base64"?}` |
| `GET /sessions` | — | `{"count", "sessions": [ConversationSummary as dict, with "session_id" = conversation_id]}` |
| `POST /sessions` | — | `{"session_id": "pending-<uuid4>"}` — a placeholder; the first `/execute` with it starts the run and returns the real id (unknown ids start new conversations, §7.8) |
| `GET /sessions/{id}` | — | `{"session": summary, "messages": [TranscriptEntry as dict]}` |
| `DELETE /sessions/{id}` | — | `forget()`; 404 if unknown |
| `POST /sessions/{id}/continue` | `{user_input, voice_mode?}` | as `/execute` (J11) |
| `POST /sessions/{id}/asks/{req_id}` | `{decision}` | as `/execute`; 409 if that ask is not pending |
| `GET /health` | — | adds `"omni": "connected" \| "unavailable"`; `alfred_backend` reports the backend |

`voice_mode` synthesis is unchanged (applied to `answer`). `/devices` keep using
`HomeAssistantIntegration` directly. Lifespan: when the backend is `omni`, create
`AlfredOmni(REPO_ROOT, settings)` and `await start()`; on failure log and keep serving
(`/execute` → 503 until a later call reconnects: `say()` retries `start()` once).
`await close()` and `decisions.close()` on shutdown.

---

## 9. Worked examples

### 9.1 "turn on the bedroom lamp" (no chat model)

```
POST /execute {"user_input": "turn on the bedroom lamp"}
bridge: start_graph_run(front-door, {message, earlier: "", continues: ""}) → run-1; GraphHost.drive(run-1)
run-1:  NodeEntered front → Decided {choice: device, confidence: 0.97} → NodeExited front/device
        NodeEntered device-action → Decided {choice: turn-on, 0.95} → NodeExited
        NodeEntered resolve (tool) → CallEnded {"outcome":"resolved","output":{entity_id: light.bedroom_lamp, entity_name: Bedroom Lamp, ...}}
        NodeEntered say-act → apply is physical → Request(permission) relayed onto run-1
bridge: quiescence = awaiting_approval → reply {answer: "Shall I turn on Bedroom Lamp?", state: awaiting_approval, session_id: run-1}
POST /execute {"user_input": "yes please", "session_id": "run-1"}
bridge: interpret → Jev {allow: 0.98} → respond(run-1, req, allow) → apply runs → CallEnded "Bedroom Lamp is on."
        run-1 RunEnded → reply {answer: "Bedroom Lamp is on.", state: ended}
```

Jev saw `inputs.message` twice and, inside `resolve_entity`, the request plus the entity
list. No chat model ran.

### 9.2 Troubleshooting over several turns

```
"the hallway sensor keeps going offline"
front → troubleshoot (graph node) → child run-2 (device-troubleshoot)
run-2: intake (converse) seed turn: reads binary_sensor.hallway_motion, asks "Is it the motion sensor by the front door?"
       FloorHeld(intake) → reply {answer: <that question>, state: awaiting_user}
"yes, the zigbee one"            → prompt(intake session) → emit_outcome(identified, "binary_sensor.hallway_motion: goes unavailable several times a day")
       inspect (silent) → triage Decided {recoverable 0.82} → recover (reload integration: physical ask)
       → reply {answer: "Shall I run reload_integration on Hallway Motion?", awaiting_approval}
"go ahead"                        → allow → recover → read-back (silent) → fixed Decided {fixed 0.9}
       → say-report speaks → run-2 ends (done) → front-door troubleshoot exits done → run-1 ends
       → reply {answer: "It was the Zigbee integration…", state: ended}
```

### 9.3 A topic change

During 9.2's intake: "actually, forget that — turn off the kitchen light". `intake`
emits `new-request` with output `"turn off the kitchen light"` → `say-bye` emits
`new-request` (silent, §7.5 rule 4) → run-2 and run-1 end. The bridge reads the output,
starts run-3 with `message="turn off the kitchen light"`, `earlier` = run-1's transcript,
`continues="run-1"`, and returns run-3's first reply ("Shall I turn off Kitchen Light?")
with `session_id="run-3"`, `continued_from="run-1"`, `new_conversation=true`.

A *small* side request inside a converse node ("and dim the hallway a bit") is handled in
place by the agent with its own tools — the prompts say so; only a different topic hands off.

### 9.4 Loading an earlier conversation

`GET /sessions` lists run-1 (ended) "the hallway sensor keeps going offline". The user
opens it (`GET /sessions/run-1` → transcript from the journal) and types "it's offline
again": the UI calls `POST /sessions/run-1/continue {"user_input": "it's offline again"}`
→ run-4 with `earlier` = run-1's transcript. `front` sees only "it's offline again" and
routes `troubleshoot`; `intake` sees `inputs.earlier` and knows which sensor. (Sending the
same text to `/execute` with `session_id=run-1` instead would start a fresh conversation —
J11.)

---

## 10. Tasks

Waves run in order; tasks inside a wave run in parallel and touch disjoint files.

### 10.0 A0 — Coordinator: kernel switch, venv install, user key config (coordinator only)

§4.1 steps 1–5. Done when step 4 prints protocol `8` and the main checkout path. Not an
implementer task; listed so dependencies are explicit.

### 10.1 A1 — Settings, hygiene and cheap bug fixes (haiku)

- **Depends on:** — (can start before A0)
- **Files:** `ai_server/config.py`, `ai_server/plugins/__init__.py`,
  `ai_server/mcp/plugins_server.py` (only if it computes its own plugins dir),
  `ai_server/forge/agents.py`, `tests/test_synthesizer.py`, `tests/conftest.py`,
  `.gitignore`, new `tests/test_config_paths.py`, `docs/architecture.md`.
- **Do:**
  1. `.gitignore`: add `jev.env`, `.omni/mcp.local.json`, `data/`.
  2. `config.py`: add, with `Field(description=...)` style comments matching the file:
     `repo_root` (not a setting — a module constant `REPO_ROOT = Path(__file__).resolve().parents[1]`),
     `jev_key_file: str = str(REPO_ROOT / "jev.env")` (env `JEV_KEY_FILE`),
     `decision_provider: str = "typesafe"`, `decision_model: str = "jev-latest"`,
     `resolve_threshold: float = 0.6`, `resolve_max_candidates: int = 200`,
     `alfred_backend: Literal["omni", "core"] = "omni"`,
     `omni_front_graph: str = "front-door"`, `omni_node_provider: Optional[str] = None`,
     `omni_node_model: Optional[str] = None`, `omni_ask_timeout: float = 300.0`,
     `omni_turn_timeout: float = 120.0`, `omni_earlier_chars: int = 6000`,
     `data_dir: str = str(REPO_ROOT / "data")`.
     Change `plugins_dir` default to `str(REPO_ROOT / "ai_server" / "plugins")`.
  3. `plugins/__init__.py`: `PluginManager()`'s default dir is the package's own directory
     (`Path(__file__).parent`), and `plugin_manager = PluginManager(settings.plugins_dir)`
     only if that does not create an import cycle — otherwise the package-dir default alone.
  4. Forge: `settings.alfred_router_model` → `settings.alfred_core_model`; the verdict check
     becomes an exact first-line match: `feedback.strip().splitlines()[0].strip().upper() == "APPROVED"`
     (so "NOT APPROVED" fails). Add a test for both in `tests/test_forge_review.py`
     (mock the LLM; no Ollama).
  5. `tests/test_synthesizer.py`: the three `TestSynthesizerSynthesize` tests skip when
     `piper` is not importable (`pytest.importorskip("piper")` inside each, or a module-level
     `skipif` on that class).
  6. `tests/conftest.py`: register the `live` marker (`pytest_configure`) and skip `live`
     tests unless `ALFRED_LIVE=1` (`pytest_collection_modifyitems`).
  7. `tests/test_config_paths.py`: `PluginManager().load_plugins()` loads ≥ 1 plugin from any
     cwd (use `monkeypatch.chdir(tmp_path)`); `settings.jev_key_file` ends with `jev.env`.
- **Docs:** `docs/architecture.md` — "Configuration" section (flip A1's block).
- **Acceptance:** gate shows 0 failed; the three synthesizer tests skip; new tests pass.

### 10.2 A2 — HA MCP tools, `decisions.py`, mock HA, `mcp.json` (sonnet)

- **Depends on:** A1 (settings fields). Needs A0 for its omni-dependent tests to run
  (they `importorskip("omni")` otherwise).
- **Parallel with:** A3, A4.
- **Files:** new `ai_server/decisions.py`, `ai_server/mcp/home_assistant_server.py`,
  new `ai_server/mcp/mock_ha.py`, `ai_server/mcp/smoke_test.py`, `.omni/mcp.json`,
  new `tests/test_decisions.py`, new `tests/test_mcp_ha_resolve.py`,
  new `tests/test_mcp_json_grades.py`, `docs/omni.md` (§ "Home Assistant tools").
- **Do:** §6 in full. Tests:
  - `decisions`: model `None` when the key file is missing; `choose` returns `(None, 0.0)`
    on any exception from a fake model; a fake model's answer above/below threshold.
    Patch `get_decision_model` — never call TypeSafe.
  - `resolve_entity`: zero / one / many candidates; resolved vs ambiguous by a fake
    `choose`; the result JSON shape exactly `{"outcome", "output"}`.
  - `apply`/`describe` against `mock_ha` (start it in-process, point the integration at it):
    `apply` flips the mock's state and returns the sentence; bad `target` → error result.
  - `mcp.json`: every HA tool name and every plugin tool name the servers expose has a
    grade entry where required (reads `read`, plugin `*_execute_command` `physical`);
    both servers have `"cwd": ".."`.
  - Keep `tests/test_mcp_stdio_subprocess.py` green (it may start the server by module;
    if `mcp.json` `cwd` changes affect it, adjust the test, not the semantics).
- **Docs:** `docs/omni.md` "Home Assistant tools" (flip A2's block).
- **Acceptance:** gate green; `python -m ai_server.mcp.smoke_test` still documented
  (it needs the omni-harness checkout; do not run it if absent — note in report).

### 10.3 A3 — Graphs, `agent.yml`, skill (sonnet)

- **Depends on:** A0 (validation needs omni importable). If A0 is not done, write the
  graphs and tests anyway; the tests `importorskip("omni")`; say so in the report.
- **Parallel with:** A2, A4.
- **Files:** new `.omni/graphs/front-door.json`, `.omni/graphs/device-troubleshoot.json`,
  `.omni/graphs/home-routine.json`, `.omni/graphs/new-capability.json`,
  `.omni/alfred/agent.yml`, `.omni/skills/home-graphs/SKILL.md`, new `tests/test_graphs.py`,
  `docs/graphs.md`.
- **Do:** §5 in full (including §5.3's decided `say-bye` shape). `tests/test_graphs.py`:
  1. every `.omni/graphs/*.json` loads (`omni.graph.schema.load_graph`) and
     `omni.graph.validate.validate(graph, library=<discover_library(repo root)>)` reports
     **no error-severity diagnostics** (warnings allowed; print them);
  2. J8 lint: every terminal node of every graph is a speaker (converse or `say-`), except
     `graph` nodes (their subgraph's terminals are checked instead) — and no non-terminal
     `agent` node is named `say-*` unless it is meant to speak (allowed; just consistent);
  3. no graph file contains a J6 house-constraint key (`cwd`, `mcp_enabled`,
     `mcp_config_path`, `on_ask`, `ask_timeout`);
  4. every decide node names `provider: typesafe`, `model: jev-latest`;
  5. every node that declares `inputs.earlier` is in a graph that receives `earlier`
     (front-door's graph nodes pass it to `device-troubleshoot` and `home-routine`).
- **Docs:** `docs/graphs.md` — one section per graph, node by node (flip A3's blocks).
- **Acceptance:** gate green with `test_graphs.py` running (not skipped) once A0 is done;
  `omni.exe graph validate` clean on all four files.

### 10.4 A4 — Bridge pure logic (sonnet)

- **Depends on:** A0 (imports `omni.kernel.events`, `omni.kernel.runs.fold`,
  `omni.graph.*`). A1 for settings.
- **Parallel with:** A2, A3.
- **Files:** new `ai_server/omni_bridge/{__init__,models,library,speakers,transcript,asks,quiescence}.py`,
  new `tests/test_omni_bridge_{library,speakers,transcript,asks,quiescence}.py`,
  `docs/omni.md` (§ "Conversations", "What Alfred says", "Asks").
- **Do:** §7.1, §7.3–§7.7 and §7.9's file format (the I/O of `forget` is A5). Build test
  events with the real dataclasses from `omni.kernel.events` and the envelope type the
  journal uses (read `omni/kernel/journal/` for the envelope class and how `fold_run`
  consumes envelopes). `library.py`: injection per §5.1 recurses into subgraphs loaded from
  the library; `write_local_mcp_config` per J7 (test with a temp repo root).
  `asks.interpret` takes `decide` as an argument (tests pass a fake).
- **Docs:** `docs/omni.md` sections named above (flip A4's blocks).
- **Acceptance:** gate green; every function in §7.1's pure modules has a test; no module
  in this task does I/O or `await`s anything except `asks.interpret` (which awaits the
  injected `decide`).

### 10.5 A5 — Bridge client (sonnet)

- **Depends on:** A2 (`decisions.choose`), A3 (graphs), A4 (pure modules).
- **Parallel with:** A6.
- **Files:** new `ai_server/omni_bridge/client.py`, new `tests/test_omni_bridge_client.py`,
  new `tests/test_omni_bridge_live.py` (`@pytest.mark.live`), `docs/omni.md`
  (§ "The bridge", "Restart").
- **Do:** §7.2, §7.4 (the I/O half), §7.8, §7.9, J16's secondary path (when `start()` has to
  spawn the daemon itself and `TYPESAFE_API_KEY` is not set, read `settings.jev_key_file`
  through `KeyFileCredential` and put it in the environment of **that spawn only** — if
  `open_client` gives no hook for the child's env, skip this and record it in §12), J18.
  Unit tests: a fake client object implementing `start_run`, `attach`, `decide_run`,
  `prompt`, `respond`, `list_runs`, `cancel_run` over scripted envelope lists; cover the five
  routing rows of §7.8, one handoff, an ask answered, re-adoption of a live run, and an
  unknown `definition_ref` being cancelled. The live test (coordinator-run) does 9.1 against
  `mock_ha` and real Jev: start the bridge with `client_factory=None`.
- **Docs:** `docs/omni.md` sections named above.
- **Acceptance:** gate green (live test skipped).

### 10.6 A6 — `main.py` endpoints (sonnet)

- **Depends on:** A1; codes against §7.2/§7.3/§8 exactly (A4 provides `models.py`; if A4 is
  not merged when A6 starts, A6 may not start — it needs the real models).
- **Parallel with:** A5.
- **Files:** `ai_server/main.py`, new `tests/test_api_omni.py`, `docs/architecture.md`
  (§ "HTTP API").
- **Do:** §8. Tests use FastAPI's `ASGITransport` (see `tests/conftest.py`) with a fake
  `AlfredOmni` injected (module-level `alfred_omni` global, like the existing globals, set by
  the test). Cover every route in §8's table, 404/503/409 mappings, `voice_mode` with a fake
  synthesizer, and that `alfred_backend="core"` still serves the old path (existing
  `tests/test_execute_*.py` keep passing).
- **Docs:** `docs/architecture.md` "HTTP API".
- **Acceptance:** gate green.

### 10.7 A7 — Live end-to-end smoke (sonnet; coordinator runs the live part)

- **Depends on:** A5, A6.
- **Parallel with:** A8.
- **Files:** new `ai_server/omni_bridge/smoke.py` (CLI: `python -m ai_server.omni_bridge.smoke`),
  `docs/omni.md` (§ "Smoke test").
- **Do:** a script that starts `mock_ha`, sets `HA_URL`/`HA_TOKEN` for the MCP servers via
  the generated `mcp.local.json` `env` (add an `env` map if the kernel's MCP config supports
  it — check `omni/driver/tools/mcp/` config parsing; otherwise document exporting them
  before starting the daemon), starts `AlfredOmni`, and drives §9.1 (with an automatic
  "yes"), §9.3's handoff and §9.4's continue; prints each reply and the run ids; exits
  non-zero on any unexpected state. Never prints keys.
- **Acceptance:** script runs to completion against fakes when `--dry` (fake client) is
  passed; the coordinator runs it live and records the result in §12.

### 10.8 A8 — Docs sweep (haiku)

- **Depends on:** A1–A6.
- **Parallel with:** A7.
- **Files:** `docs/architecture.md`, `docs/omni.md`, `docs/graphs.md`, `README.md`,
  `ARCHITECTURE.md`, `CODEBASE_SUMMARY.md`, `QUICKSTART.md`.
- **Do:** every `Planned (omni-jev` block is either flipped (if its task built it — check
  the code) or rewritten as "Not built (omni-jev): …". README: a short "Alfred on Omni"
  section pointing to `docs/omni.md` and `docs/graphs.md`, replacing the AlfredCore
  description. `ARCHITECTURE.md`, `CODEBASE_SUMMARY.md`: a banner at the top
  "Stale: describes the pre-Omni AlfredRouter/AlfredCore design. Current: docs/architecture.md."
  `QUICKSTART.md`: setup steps from §4.1 (without secrets).
- **Acceptance:** `grep -rn "Planned (omni-jev" docs README.md` is empty.

### 10.9 A9 — Retire AlfredCore and the SQLite session store (sonnet; coordinator may defer)

- **Depends on:** A7's live run passing (coordinator decides).
- **Files:** delete `ai_server/core/`, `ai_server/alfred_router/`, `ai_server/intent_processor.py`,
  `ai_server/memory/`; `ai_server/main.py`, `ai_server/config.py` (drop core/session
  settings and `alfred_backend`), the tests that only test deleted code
  (`tests/test_core.py`, `tests/test_execute_core_mode.py`, `tests/test_execute_with_session.py`,
  `tests/test_session_store.py`, `tests/test_context_provider.py` — check each), `requirements.txt`
  (drop `langchain*` only if nothing else imports them: the Forge does — keep them while
  `ai_server/forge/` exists), `docs/architecture.md`.
- **Do:** J15. `grep -rn "alfred_core\|AlfredCore\|SessionStore\|intent_processor\|alfred_router" ai_server tests` empty afterwards (except the Forge if it still references
  something — then leave the Forge out and record it).
- **Acceptance:** gate green; `/execute` served only by the bridge.

### 10.10 A10 — Local `CLAUDE.md` rewrite (haiku; coordinator-run, not committed)

- **Depends on:** A8 (and A9 if done).
- **Files:** `C:\Users\udayr\Documents\Projects\alfred\CLAUDE.md` (git-ignored; edit in the
  **main checkout**, never commit).
- **Do:** replace it with a short file (≤ 80 lines): what Alfred is now (one paragraph),
  the conductor rules section (keep it verbatim), the never-commit list, how to run the
  server and tests, and a table pointing to `docs/architecture.md`, `docs/omni.md`,
  `docs/graphs.md`, `docs/omni-jev-plan.md` (+ index) for everything else.

---

## 11. Out of scope, and kernel follow-ups noticed

- Voice client over the native codec (migration 5.4); `alfred-ui` on AG-UI (5.5) and any
  `alfred-ui` change (a "continue this conversation" button and approval buttons would use
  §8's new routes).
- **Kernel follow-ups (do not implement here):**
  1. JSON-object tool outputs are not indexable on the blackboard (`outputs.resolve.entity_id`
     fails); J4 works around it by passing the whole string.
  2. A terminal node cannot branch, so carrying a converse node's `new-request` out of a
     subgraph costs a `say-bye` node (§5.3).
  3. Held floors are not re-adopted at daemon boot (kernel §10 T7): a parked conversation's
     `floor_timeout` only arms once something adopts it. Alfred's J18 adopts the *graph
     host*, not the floor — the deadline is the daemon's.
  4. An out-of-process decide node resumed between `NodeEntered` and `Decided` exits `error`
     (kernel §10 T6) — Alfred's graphs route `error` to an LLM fallback, so this degrades
     rather than fails.

---

## 12. Decision log (implementers append)

| Task | Change to the plan | Why |
|---|---|---|
| plan | Baseline recorded: `alfred\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` → 93 passed, 3 failed (piper), 6 skipped, 5.5 s. | So every task can show it did not regress. |
| plan | The §5.2 `front-door` and §5.3 `device-troubleshoot` JSON were validated by the planner with the kernel at `808d4cb` (`omni.graph.validate.validate(graph, library=…)` over a library holding all four graphs): no findings. The validator does **not** check a `graph` node's declared `outcomes` against the subgraph's terminal outcomes — keep them in step by hand (A3 test 1 cannot catch a mismatch). | So A3 starts from JSON known to validate. |
| coordinator | **J15 / A9 deferred to the user.** AlfredCore and the SQLite store are not deleted on this branch; `settings.alfred_backend` (`omni` default, `core` fallback) stays. A9 runs only when the user says so. | Deleting a working brain is the one hard-to-reverse product call in the plan, and nothing else depends on it. |
| coordinator | **J16 live path:** the coordinator does not create `~/.omni/providers/typesafe/config.yml` (agents may not edit `~/.omni/`). For live runs the daemon is started with `TYPESAFE_API_KEY` set from `jev.env` in-process (never printed), i.e. the plan's secondary path. The user step in §4.1.5 remains the recommended permanent setup. | Keeps the hard rule; lets A7's live smoke exercise real Jev decide nodes tonight. |
| A1 | Settings are declared with `#` comments like the rest of `config.py`, not `Field(description=...)`. | Matches the file; the plan's wording was ambiguous. |
| A1 | Forge verdict is `_is_approved()`: first line stripped and upper-cased equals `APPROVED`, and an empty reply is "not approved". The plan's inline `splitlines()[0]` would raise `IndexError` on an empty reply. | Same rule as the plan, without the crash. |
| A1 | Piper skip is per test (`@requires_piper` on the three tests that fail without piper), not a class-level skip. The three that mock the voice keep running. | Keeps the coverage that does not need piper. Gate is 107 passed, 9 skipped, 0 failed: the 93 baseline plus A1's 14 new tests. |
| A1 | `ai_server/mcp/plugins_server.py` is not edited. Its module docstring still calls the `plugins_dir` default out of scope, which is now stale, but it already passes an absolute dir. | A2 owns that file; avoids a merge conflict. |
| A0 (coordinator) | Done: `omni-kernel` main checkout is on `feat/model-kinds` @ `53a9923` (kernel gate 4089 passed / 11 skipped; live Jev 3 passed); the `ok-model-kinds` worktree was removed to free the branch; `pip install -e` of the kernel into `alfred\.venv`; `import omni` → main checkout, `PROTOCOL_VERSION` 8 in both venvs; no daemon was running. | Wave-0 gate. |
| A2 | `mock_ha.start_mock_ha()` returns `(url, server)` (the plan's order), the reverse of the old `smoke_test.start_mock_ha()`. State is per server instance (`server.states`, `server.calls`), not module-global; a service call on an unknown entity answers `200 []`, like real Home Assistant. | Tests need isolation, and `apply` needs an observable state. |
| A2 | `apply` answers with the state Home Assistant returned after the call (`"<entity_name> is <state>."`) and is an error result when that state cannot be read back (e.g. unknown entity), because the integration reports success for any `200`. `describe` only shows `(last changed HH:MM)` when `last_changed` is in the entity's attributes, as the plan words it (real HA keeps it top-level, so it is usually absent). | Matches the plan's contract without editing `integration/home_assistant.py`. |
| A2 | `resolve_entity` for `status` reads `/api/states` itself (via the integration's session) to include `sensor`, `binary_sensor`, `lock`, because `discover_devices()` is hard-wired to five domains and the integration is not in A2's file list. An `ambiguous` outcome lists the first five entity ids (`candidates`) and a `reason`; `decisions.choose` returns no probabilities, so "top 5 by probability" is not available. Extra `reason` key is additive. | Keeps the edit inside A2's files; the plan allowed "else first 5". |
| A2 | `decisions.choose` returns `(None, confidence)` (not `(None, 0.0)`) when the answer is below the threshold; `(None, 0.0)` only for unavailable/raised. A `None` model is not cached, so a key file created later is picked up. | Callers (the ask interpreter) can log how close a miss was; no restart needed after creating `jev.env`. |
| A2 | `ai_server/mcp/smoke_test.py` now uses the `omni` installed in the venv (omni-kernel) for its MCP client, with `--omni-harness` an optional override; and passes `cwd` in `McpServerConfig`. The `../omni-harness` checkout holds only `__pycache__`, so the old default could not run. It sets `JEV_KEY_FILE` to a nonexistent file for the server subprocess so a smoke run never reaches real Jev. | Ran it: PASS, 19 tools. |
| A2 | Gate after A2: 147 passed, 9 skipped, 0 failed (A1's 107 plus 40 new). No live test added: nothing in A2's card needs one. | |
