# Alfred on Omni

Alfred is a system built around omni-kernel: the kernel supplies the mechanisms (sessions,
runs, graphs, decide nodes, conversational nodes, the journal, permission grades), and
Alfred supplies the policy (its graphs, its tools, how a conversation starts and ends).
The design and its decisions are in [omni-jev-plan.md](omni-jev-plan.md).

## Setup

> **As built (omni-jev, A0).** omni-kernel's main checkout is `C:\Users\udayr\Documents\Projects\omni-kernel`
> on `feat/model-kinds`, installed editable into Alfred's venv:
>
> ```
> C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m pip install -e C:\Users\udayr\Documents\Projects\omni-kernel
> C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -c "import omni, omni.codec.client, omni.graph.host; from omni.kernel.api import PROTOCOL_VERSION; print(omni.__file__, PROTOCOL_VERSION)"
> ```
>
> The check prints the main checkout path and `8`. A daemon left running from before must be
> stopped first (`omni daemon stop`): a protocol-7 daemon refuses a protocol-8 client. The bridge
> also needs `prompt_toolkit` (the kernel's `[cli]` extra), which is installed in Alfred's venv.
>
> **One-time user step (J16).** Agents may not create this file, so you create it:
> `C:\Users\udayr\.omni\providers\typesafe\config.yml`, containing the single line
>
> ```
> api_key_file: C:\Users\udayr\Documents\Projects\alfred\jev.env
> ```
>
> The file names the key file; it never contains the key. Until it exists, every Jev `decide`
> node exits `error` and each graph takes its LLM fallback path.
>
> **Alternative.** Set `TYPESAFE_API_KEY` in the daemon's environment (the value comes from
> `jev.env`; do not print or commit it). If no daemon runs and `TYPESAFE_API_KEY` is unset, the
> bridge starts the daemon with the key from `settings.jev_key_file` in that child's environment
> only (A5, the J16 secondary path). A daemon someone else started is not given the key.
> Worktrees have no `jev.env`; pass the main checkout's file where a flag asks for it.

## MCP servers

`.omni/mcp.json` declares `home_assistant` and `alfred_plugins`, with grades: Home
Assistant writes are `physical` (ask every call), reads are `read`.

> **As built (omni-jev, A2).** Both servers carry `"cwd": ".."` (resolved against the file's
> directory, i.e. the repo root), so `omni --mcp` from a shell finds the `ai_server` package.
> Plugin grades use the per-server prefixed names, one set per plugin in `ai_server/plugins/`
> (`<slug>_get_device_info`, `_discover_devices`, `_health_check` are `read`;
> `<slug>_execute_command` is `physical`). `home_assistant` keeps `grade: physical` as the
> server default, so a tool without its own entry asks every call. `tests/test_mcp_json_grades.py`
> fails if either server exposes a tool the file does not grade, or lists one it no longer
> exposes. **Adding a plugin means adding its four slugged entries to `mcp.json`.**

> **As built (omni-jev, A4).** The bridge generates the git-ignored `.omni/mcp.local.json` from
> this file on `start()` (`library.write_local_mcp_config`, J7). Each `python` command becomes
> Alfred's own interpreter and every server's `cwd` becomes the absolute repo root; the daemon
> spawns the MCP servers from that generated file.

## Home Assistant tools

> **As built (omni-jev, A2).** Besides the existing `turn_on`, `turn_off`, `toggle`,
> `set_brightness`, `set_temperature`, `get_state`, `list_entities` and `health_check`, the
> server (`ai_server/mcp/home_assistant_server.py`) exposes the LLM-free device path (J4):
>
> | Tool | Grade | Takes | Returns |
> |---|---|---|---|
> | `resolve_entity` | read | `request`, `action` (`turn-on`, `turn-off`, `toggle`, `status`) | JSON object `{"outcome": "resolved"\|"ambiguous"\|"none", "output": {...}}` |
> | `apply` | physical | `action` (`turn-on`, `turn-off`, `toggle`), `target`, `request` | one sentence, `Bedroom Lamp is on.`; errors are `isError` results |
> | `describe` | read | `target` | one sentence, `Bedroom Lamp is off (last changed 14:02).` |
>
> `list_entities` (fixed in A2b) lists `light`, `switch`, `fan`, `climate`, `cover`, `sensor`,
> `binary_sensor`, `lock`, `media_player` and `vacuum`, each with `entity_id`, `name`,
> `device_type` and `state`. Before A2b it listed only the first five, so the agent could not
> see any sensor. The optional `domain` argument filters to one of those domains, and an
> unlisted domain gives an empty list. If Home Assistant cannot be read, the tool is an error
> result, not an empty list.
>
> `resolve_entity` lists the devices that support the action (`status` also looks at
> `sensor`, `binary_sensor` and `lock`). No candidates: `none` (`reason: no matching devices`).
> Exactly one: `resolved` with confidence 1.0, no model call. More than
> `settings.resolve_max_candidates` (200): `ambiguous`, `reason: too many devices`. Otherwise
> one Jev `choice` question through `ai_server/decisions.py` (`choose`, threshold
> `settings.resolve_threshold`), whose options are `entity_id` -> `<name> - <domain>, currently
> <state>`. A confident answer is `resolved` with `{entity_id, entity_name, domain,
> confidence}`; anything else (below threshold, model error, no key file, omni not importable)
> is `ambiguous` with `candidates` (the first five entity ids), which the graph routes to the
> LLM device agent. The tools never raise on a missing key.
>
> A tool node's output cannot be indexed, so `resolve_entity`'s `output` is passed **whole**
> as the string `target` of `apply` / `describe`, which parse it (an object with `entity_id`
> and `entity_name`). The permission prompt for `apply` therefore shows the resolved
> device's name. `apply` reports the state Home Assistant returned after the call.
>
> `ai_server/decisions.py` is the one door to a decision model (J5): `get_decision_model()`
> (a cached `TypeSafeDecision` on `KeyFileCredential(settings.jev_key_file)`, or `None`),
> `choose(question, options, state, *, threshold)` -> `(answer | None, confidence)` (never
> raises; logs only the exception class) and `close()`. `ai_server/mcp/mock_ha.py` is the
> shared mock Home Assistant (`start_mock_ha() -> (url, server)`, per-server `states`; service
> calls really change state) used by the unit tests and `python -m ai_server.mcp.smoke_test`,
> which drives both servers through omni's own MCP client (the `omni` installed in the venv).

## Conversations

> **As built (omni-jev, A4, A5).** The pure logic is in `ai_server/omni_bridge/`; the I/O that
> drives it is `client.py` (A5).
>
> - **One conversation = one run of `front-door`** (J1); its id is the run id. When the graph
>   exits the conversation is over.
> - **Transcript** (`transcript.build`): rebuilt from the journal alone, so it survives a
>   restart. It is the user's `inputs.message`, then, in journal order across the run and its
>   child runs, what each speaker node said, what the person said to a converse node after
>   its seed prompt, and each answer to an ask as `(approved: <decision>)` (a refusal reads
>   `(declined)`). A `graph` node's `NodeEntered.session_id` is the child run's id, which is
>   how the walk descends into subgraphs.
> - **Loading an earlier conversation** (J11/J12): `transcript.earlier_text` renders
>   `role: text` lines and keeps the **newest** `settings.omni_earlier_chars` characters;
>   that string is `inputs.earlier` of the new run. Every run has the same three inputs
>   (`message`, `earlier`, `continues`).
> - **Handoff** (J10): a converse node that hears a different request exits `new-request`
>   with the request as its output. `transcript.handoff_text` reads those words back from
>   the ended run's `output_ref`; the client starts the next run with them.
>   (Fixed in A5b.) Handoff suppression applies to the **reply** only: the words a terminal
>   `new-request` speaker said stay in the transcript and in `earlier` (`speakers.Item.suppressed`
>   is set for them; `spoken` skips them, `walk`/`transcript.build` keep them).
> - **Listing**: `transcript.summarize` builds a `ConversationSummary` (title = first 60
>   characters of `inputs.message`) from a run fold.
> - **Hidden conversations** (`models.parse_hidden` / `render_hidden`): the file
>   `<data_dir>/hidden_conversations.json` is a JSON list of run ids; the journal is never
>   edited.
> - **When `say()` returns** (`quiescence.state_of`): `ended` when the root run ended;
>   `awaiting_approval` when any run has an unanswered `Request`; `awaiting_user` when a run
>   holds the floor and that session's latest turn has ended **without calling
>   `emit_outcome`** (fixed in A5c). `FloorHeld` is written once per node entry, not per
>   reply, so the rule is the fold plus the session's last turn, never the event alone. Right
>   after prompting a floor session, pass `since` so the previous (ended) turn is not
>   mistaken for the answer.
>
>   A floor node's turn that ended after a successful `emit_outcome` is *leaving*
>   (`TurnState.leaving`, read from the `CallStarted`/`CallEnded` of `emit_outcome` in that
>   turn). The kernel records the turn's end before it harvests the outcome and writes
>   `NodeExited`, and until then the fold still shows the floor, so such a turn used to read
>   as `awaiting_user` (live: "forget that, turn on the coffee maker" came back with the
>   intake's goodbye and the handoff never ran). Now it is never `awaiting_user`: `say()`
>   keeps reading until the node exits and the walk parks again (another floor or an ask),
>   the run ends (then the J10 handoff runs inside the same call, reply `session_id` = the
>   new run, `continued_from` = the old), or `omni_turn_timeout` returns `working`. A
>   refused `emit_outcome` routes nothing and so still leaves the floor with the person.
>   No sleep or settle is involved; the journal decides.
>
> **As built (omni-jev, A5).** `AlfredOmni.say(text, conversation_id)` routes per plan section 7.8:
>
> | The id is... | What happens |
> |---|---|
> | `None`, unknown, hidden | a new run (`new_conversation: true`) |
> | a live run with a pending ask | the message is the answer (see Asks), then wait |
> | a live run holding the floor | `client.prompt(floor_session, text)`, then wait |
> | a live run still working | `state: "working"`, `I'm still on it.` (never queued; speech that arrived after an earlier timeout is delivered instead) |
> | an ended run | a **fresh** conversation, no seed (J11) |
>
> A call for a conversation whose previous `say()` is still waiting also answers `I'm still on
> it.` immediately. "Wait" is quiescence (section 7.4) bounded by `settings.omni_turn_timeout`
> (J19); `state: "working"` on timeout. Before a reply is composed the bridge makes sure
> every stream it attached has been read up to the head it had at attach time, so a run that
> ended in milliseconds is not reported with its last node's words missing.
>
> - **Handoff** (J10): when the run ends with a terminal `new-request`, `say()` reads the
>   words back, builds `earlier` from the ended run's transcript, starts the next run and
>   returns its reply with the **new** `conversation_id`, `continued_from` = the old id and
>   `new_conversation: true`; the old run's last words, if any, come first. At most one
>   automatic handoff per call. A terminal speaker that exits `new-request` is silent in
>   the reply (A4) so Alfred does not repeat himself, but (fixed in A5b) everything it and
>   the earlier turns of that run said is in the `earlier` seed of the handoff run and of
>   `continue_from`.
> - **`continue_from(id, text)`** (J11): a new run with `inputs.earlier` = the old
>   conversation's transcript (newest `omni_earlier_chars`), `continues` = the old id. The old
>   id may be live, ended or from before a restart; unknown or hidden raises
>   `ConversationNotFound`.
> - **`list_conversations()`** shows front-door runs only (those carrying `message` and
>   `continues` inputs; subgraph runs do not), newest first, from the journal; ended runs'
>   folds are cached. **`transcript(id)`** replays the run, its child runs and speaker
>   sessions from the journal. A run whose graph has since been edited keeps its user
>   messages and answers but loses node speech (its `definition_ref` no longer resolves).
> - **`forget(id)`**: cancels the run if live (`cancel_run(id, "forgotten")`) and adds the id
>   to `<data_dir>/hidden_conversations.json`; `False` for an id that is not a conversation.
>   Hidden ids are skipped by listings and start fresh conversations if used.
> - **Errors:** `ConversationNotFound` (`LookupError`) and `BridgeUnavailable`
>   (`RuntimeError`) live in `omni_bridge.models` and are re-exported by the package;
>   `ValueError` for a bad `answer_ask`.

## What Alfred says

> **As built (omni-jev, A4).** A node speaks iff it is an `agent` or `tool` node that is
> `converse: true` or whose id starts with `say-` (J8; `speakers.is_speaker`). A speaking
> agent contributes the text of its assistant messages (never the kernel's seed prompt, the
> person's words, reasoning or tool-use blocks); a speaking tool contributes its output, or
> `I couldn't do that — <reason, 200 characters>` when it failed (a call the person
> refused or that timed out says `Okay, I didn't do that.`). A terminal speaker that exits
> `new-request` is silent in the reply (the handoff's first reply is what the person hears;
> fixed in A5b: its words are still in the transcript and the `earlier` seed). Pieces are
> joined with a blank line (`speakers.compose_reply`); with nothing said and an ask pending,
> the reply is the ask's question, and with both the question is appended. `speakers.spoken`
> takes `since` (stream id -> last seq seen) so a reply holds only what was said after the
> work was submitted. A handoff never prefixes the fallback apology (fixed in A5d): a run that
> ends `new-request` and said nothing gets no went-wrong text, only the new run's reply.

## Asks

> **As built (omni-jev, A4).** `asks.render(request, stream_id, node_lookup)` turns a
> kernel `Request` into a `PendingAsk`:
>
> - **permission**: `Shall I turn on Bedroom Lamp?` for `mcp__home_assistant__apply` (the verb
>   is its `action`, the name is `entity_name` in its `target`), `Shall I run <tool> on
>   <entity_name>?` for any tool with an `entity_name` argument, otherwise `Alfred wants to
>   use <tool>. Allow it?`. Options are `allow`/`deny`.
> - **approval** (a `human` node): its prompt, each shown value trimmed to 400 characters, then
>   `(a / b)`; the options are the node's outcomes, with their descriptions and the
>   `on_timeout` fallback carried along.
> - **elicit**: the server's message; the reply text is the answer's data.
>
> `asks.interpret(pending, text, decide)` asks Jev (`decide`, injected; in production
> `ai_server.decisions.choose`) with threshold 0.75 (J13). `asks.step` then decides what to
> do: a clear answer resolves the ask; an unclear one re-asks once (`Sorry — was that a
> yes or a no?`); a second unclear answer denies (an approval takes its `on_timeout` outcome,
> else its last outcome) and says nothing was done. A refused permission always says so
> (J14).
>
> **Decision words.** The kernel's permission gate reads `once`/`always`/`deny` (anything
> else denies), so Alfred's `allow` is sent as `once` (`asks.decision_word`); `always` is
> never sent, because a physical call is approved per call. A sandbox ask sends
> `allow`/`deny` as they are. An approval answers with the request's own outcome names.
>
> **As built (omni-jev, A5).** A relayed ask is a `Request` on the run's (or a child run's)
> stream; the bridge answers it with `client.respond(stream_id, req_id, word, data=...)` where
> `word` is `asks.decision_word` (`allow` is sent as `once`). The next `/execute` message is
> the answer (J14): `say()` calls `asks.interpret`, then `asks.step`. A clear answer is sent
> and the bridge waits until it has read the `Response` back (the old `Request` is still in
> its mirror until then) before judging the run; an unclear one returns the re-ask with the
> same `pending` (`reasks` is kept between messages for as long as the process lives; after
> a restart the count starts again). A refusal's `Okay, I didn't do that.` is not repeated
> when the denied tool node says it too. `answer_ask(id, req_id, decision)` is the direct
> path for UIs with buttons: `decision` must be one of the pending ask's options (`allow` /
> `deny` for permissions); `ValueError` if `req_id` is not the conversation's pending ask or
> the decision is not an option; `ConversationNotFound` for a conversation that is not live.
> If the kernel reports the ask already answered (for example it timed out), the bridge
> carries on and reports what happened.

## The bridge

> **As built (omni-jev, A5).** `ai_server/omni_bridge/client.py`: `AlfredOmni(repo_root,
> settings, *, decide=decisions.choose, client_factory=None)`.
>
> - **`start()`** writes `.omni/mcp.local.json`, loads the graph library (constraints
>   injected, J6), connects, and re-adopts live runs. **`close()`** cancels the host tasks and
>   closes the client; the daemon and its runs are untouched. **`healthy()`** is a cheap
>   `list_runs` that must answer within 5 s.
> - **Connection.** With no `client_factory` the bridge uses
>   `omni.cli.connect.open_client(spawn=True)`, which starts the daemon if none runs
>   (`prompt_toolkit`, the kernel's `[cli]` extra, is installed in Alfred's venv, so
>   `omni.cli` imports and the J16 key path below works). Only in a venv without it does the
>   bridge fall back to joining an **already running** daemon through `omni.codec`
>   (discovery file, probe, `NativeClient.connect`), raising `BridgeUnavailable` if there is
>   none. There is no automatic reconnect: after a lost
>   connection calls raise `BridgeUnavailable` until the process restarts.
> - **Jev key for a daemon the bridge starts (J16, secondary path).** If no daemon is running
>   and `TYPESAFE_API_KEY` is not set, `start()` reads `settings.jev_key_file` through
>   `KeyFileCredential` and spawns the daemon with the key in **that child's** environment
>   (`ensure_daemon(spawn=...)`); it is never logged or kept. A daemon someone else started is
>   not touched, so the permanent setup is still `~/.omni/providers/typesafe/config.yml`.
> - **Streams.** Per conversation one *mirror*: the root run, each child run (found through
>   `NodeEntered.session_id`, which is a run id for a `graph` node) and each **speaking**
>   node's session stream, each read by its own task (a stream nobody reads stalls the whole
>   connection). Silent nodes' sessions are never attached. A `GraphHost` per root run drives
>   the graph and adopts child runs itself.

## Restart

> **As built (omni-jev, A5; scope fixed in A5b).** The bridge keeps a record of the live runs
> **it started**, `<data_dir>/bridge_runs.json` (`{run id: graph name}`, written next to
> `hidden_conversations.json`; a missing or corrupt file means "started nothing"). On
> `start()` it lists live `client:graph` runs (`list_runs(module="client:graph")`) and looks
> **only** at the runs in that record; a run it did not start (another client's, or a
> subgraph's child run) is never adopted, re-driven or cancelled. Per recorded run:
>
> - no longer live (or forgotten): pruned from the record;
> - its history still fits the graph of the **same name** in the library: re-adopted. A mirror
>   is built from the journal and a `GraphHost` resumes it. When the run's `definition_ref`
>   is no longer in the library (the graph file, or a house setting that feeds constraint
>   injection such as `omni_ask_timeout`, `omni_node_provider/model` or the repo path,
>   changed: the ref is a content hash of the injected graph), the old ref is aliased to the
>   same-name graph (and likewise for its subgraph runs, found through the `graph` nodes) so
>   the policy can keep driving it. Its history is treated as already delivered, and a pending
>   ask is re-rendered from the journal, so the next message answers it;
> - the stored name is gone from the library, or the run entered a node the graph no longer
>   has (or a `graph` node changed kind): cancelled with reason `graph changed` (J18) and
>   pruned.
>
> The record gains a run when the bridge starts it (a new conversation or a handoff) and
> loses it when the conversation ends, is forgotten or is cancelled. Runs started before the
> record existed are not in it and are left alone. Ended conversations need no adoption:
> listings and transcripts are rebuilt from the journal on demand.

## Smoke test

> **As built (omni-jev, A7, A7b, A7c).** `ai_server/omni_bridge/smoke.py` drives plan sections 9.1 to 9.4
> through a real omni daemon and the real `front-door` graph, with Alfred's Home Assistant MCP
> server pointed at the **mock** Home Assistant (no real house). Run from the repo root:
>
> ```
> C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m ai_server.omni_bridge.smoke --dry-run
> C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m ai_server.omni_bridge.smoke
> C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m ai_server.omni_bridge.smoke --jev --jev-key-file C:\Users\udayr\Documents\Projects\alfred\jev.env
> ```
>
> * `--dry-run` needs no secrets and makes no model call: it loads the graph library and checks the
>   house constraints were injected, writes `.omni/mcp.local.json` with the mock's `HA_URL`,
>   starts the mock, starts a daemon and connects, starts the Home Assistant MCP server from the
>   generated file and lists the mock's entities, then stops everything.
> * The second command is the full run on the LLM path: decide nodes fall back to the chat model.
>   It needs the node LLM's key (`provider`/`model` come from `.omni/alfred/agent.yml`, or
>   `OMNI_NODE_PROVIDER`/`OMNI_NODE_MODEL`; the script prints which, and exits 2 with a message
>   instead of hanging if that provider has no key).
> * `--jev` uses real Jev for decide nodes. The key file is only a *path* to the script: the
>   bridge's J16 path reads it through `KeyFileCredential` and puts it in the environment of the
>   daemon it spawns. Nothing is printed. In a worktree pass the main checkout's `jev.env`
>   (`--jev-key-file`), since a worktree has none. Without `--jev` the script removes
>   `TYPESAFE_API_KEY` from its environment and points the key setting at a missing file.
> * Live runs in this environment use DeepSeek for the agent nodes (the Anthropic key here is
>   rejected): prefix the command with `OMNI_NODE_PROVIDER=deepseek OMNI_NODE_MODEL=deepseek-v4-flash`
>   (PowerShell: set `$env:OMNI_NODE_PROVIDER` / `$env:OMNI_NODE_MODEL` first). The banner prints the
>   effective `provider/model` after the override, and whether its key was found.
> * `--only 9.1` runs one scenario (9.4 adds 9.1, whose ended conversation it continues; 9.2 and 9.3
>   are self-contained).
>   `--timeout` (default 900 s) is a hard overall limit; `--turn-timeout` bounds one bridge call.
>   `--shared-daemon` uses your normal daemon instead of a private one.
>
> The daemon is **isolated**: the script sets `OMNI_HOME` and the journal location
> (`LOCALAPPDATA` / `XDG_STATE_HOME`) to a temp directory before connecting, so your own daemon,
> journal and conversations are untouched (provider config still comes from `~/.omni/providers`).
> The daemon is stopped and the temp directory removed on every exit path, and `mcp.local.json`
> is regenerated clean.
>
> Scenarios (the mock is seeded with several healthy sensors, so a report that names no device
> cannot be solved by reading the house): **9.1** "turn on the bedroom lamp" then an automatic yes
> (words through Jev with `--jev`, otherwise `answer_ask(allow)`), checking no chat node ran and the
> mock's lamp is on; **9.2** "something in the house is not working and I don't know what" makes the
> troubleshooting intake ask, then "it's the garage motion sensor, it keeps going offline" is taken
> (same conversation; `intake=identified` or another question); **9.3** a chat that always holds the
> floor ("let's have a proper chat ... wait for my answer", routed `chat` by `front`), then
> "actually, turn on the coffee maker": the reply must come from a NEW conversation
> (`continued_from`, `new_conversation`) whose `front` routed `device`, offering the physical ask,
> which is refused (Alfred says he did not do it; the mock stays off); **9.4** 9.1's ended conversation
> is listed `ended`, its transcript is readable, and `continue_from("that lamp keeps going offline")`
> starts a new linked run whose `inputs.earlier` carries it (plumbing only, not the model's routing
> or wording). `--extras` adds the optional **9.3x**, the same handoff out of the troubleshooting
> intake, which depends on the model choosing to ask. 9.2 (2 tries) and 9.3 (2 tries) open a second
> fresh conversation if the model did not hold the floor, and print each attempt. Each scenario prints
> PASS or FAIL, the reply text, the path from the journal (node ids, `Decided` choice with confidence,
> provider/model and latency, tool calls, child-run nodes prefixed with their run id) and the latency;
> a failure message carries the path taken. Exit status: 0 pass, 1 a scenario
> failed, 2 a precondition is missing, 3 the overall timeout fired.

## Secrets

`jev.env` (the raw TypeSafe key) lives only in the main checkout and is git-ignored. Code
reads it only through omni's `KeyFileCredential`. Never print or commit it.
