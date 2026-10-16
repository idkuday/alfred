# Alfred on Omni

Alfred is a system built around omni-kernel: the kernel supplies the mechanisms (sessions,
runs, graphs, decide nodes, conversational nodes, the journal, permission grades), and
Alfred supplies the policy (its graphs, its tools, how a conversation starts and ends).
The design and its decisions are in [omni-jev-plan.md](omni-jev-plan.md).

## Setup

> **Planned (omni-jev, A0/A8).** omni-kernel's main checkout on `feat/model-kinds`,
> installed editable into Alfred's venv; `~/.omni/providers/typesafe/config.yml` with
> `api_key_file:` pointing at `jev.env` (never committed). See the plan §4.1.

## MCP servers

`.omni/mcp.json` declares `home_assistant` and `alfred_plugins`, with grades: Home
Assistant writes are `physical` (ask every call), reads are `read`.

> **Planned (omni-jev, A2/A4).** Both servers get `"cwd": ".."`; the bridge generates the
> git-ignored `.omni/mcp.local.json` with Alfred's venv Python and the absolute repo root
> (J7). Plugin grades use the prefixed per-server names; plugin `*_execute_command` is
> `physical`.

## Home Assistant tools

> **Planned (omni-jev, A2).** New tools `resolve_entity` (read; Jev `choice` over the live
> entity list, inside the server via `ai_server/decisions.py`), `apply` (physical; takes
> `resolve_entity`'s output whole as `target`) and `describe` (read). See the plan §6.

## Conversations

> **As built (omni-jev, A4).** The pure logic is in `ai_server/omni_bridge/`; the I/O that
> drives it is A5's `client.py`.
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
> - **Listing**: `transcript.summarize` builds a `ConversationSummary` (title = first 60
>   characters of `inputs.message`) from a run fold.
> - **Hidden conversations** (`models.parse_hidden` / `render_hidden`): the file
>   `<data_dir>/hidden_conversations.json` is a JSON list of run ids; the journal is never
>   edited.
> - **When `say()` returns** (`quiescence.state_of`): `ended` when the root run ended;
>   `awaiting_approval` when any run has an unanswered `Request`; `awaiting_user` when a run
>   holds the floor and that session's latest turn has ended. `FloorHeld` is written once per
>   node entry, not per reply, so the rule is the fold plus the session's last turn, never the
>   event alone. Right after prompting a floor session, pass `since` so the previous (ended)
>   turn is not mistaken for the answer.
>
> **Planned (omni-jev, A5).** `POST /sessions/{id}/continue`, the routing in `say()`, and
> `forget()` (cancel the run and add its id to the hidden file). See the plan §7.8.

## What Alfred says

> **As built (omni-jev, A4).** A node speaks iff it is an `agent` or `tool` node that is
> `converse: true` or whose id starts with `say-` (J8; `speakers.is_speaker`). A speaking
> agent contributes the text of its assistant messages (never the kernel's seed prompt, the
> person's words, reasoning or tool-use blocks); a speaking tool contributes its output, or
> `I couldn't do that — <reason, 200 characters>` when it failed (a call the person
> refused or that timed out says `Okay, I didn't do that.`). A terminal speaker that exits
> `new-request` is silent: the handoff's first reply is what the person hears. Pieces are
> joined with a blank line (`speakers.compose_reply`); with nothing said and an ask pending,
> the reply is the ask's question, and with both the question is appended. `speakers.spoken`
> takes `since` (stream id -> last seq seen) so a reply holds only what was said after the
> work was submitted.

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
> **Planned (omni-jev, A5).** Routing the next `/execute` message through this, the
> direct `POST /sessions/{id}/asks/{req_id}` answer, and the `respond` call itself. See the
> plan §7.6 and §7.8.

## The bridge

> **Planned (omni-jev, A5).** `ai_server/omni_bridge/client.py` — `AlfredOmni`: one
> `NativeClient`, a `GraphHost` per live run, a reader task per attached stream. See the
> plan §7.2.

## Restart

> **Planned (omni-jev, A5).** On start-up the bridge re-adopts live front-door runs whose
> graph it still has and cancels those whose graph changed (J18).

## Smoke test

> **Planned (omni-jev, A7).** `python -m ai_server.omni_bridge.smoke` drives a device
> command, a handoff and a continue against the mock Home Assistant.

## Secrets

`jev.env` (the raw TypeSafe key) lives only in the main checkout and is git-ignored. Code
reads it only through omni's `KeyFileCredential`. Never print or commit it.
