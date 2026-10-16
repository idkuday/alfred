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

> **Planned (omni-jev, A4/A5).** One conversation = one run of `front-door`; its id is the
> run id. When the graph exits, the conversation is over. Loading an earlier conversation
> starts a new run seeded with the old transcript (`inputs.earlier`). A converse node that
> hears a new request ends with `new-request` and the bridge hands off to a new run. See
> the plan §7.7–§7.8 and §9.

## What Alfred says

> **Planned (omni-jev, A4).** A node speaks iff it is `converse` or its id starts with
> `say-` (J8). See the plan §7.5.

## Asks

> **Planned (omni-jev, A4/A5).** Permission and approval requests are turned into a
> question; the user's next message is interpreted by Jev (`allow`/`deny`, or the human
> node's outcomes). See the plan §7.6.

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
