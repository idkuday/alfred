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

> **As built (omni-jev, A2).** Both servers carry `"cwd": ".."` (resolved against the file's
> directory, i.e. the repo root), so `omni --mcp` from a shell finds the `ai_server` package.
> Plugin grades use the per-server prefixed names, one set per plugin in `ai_server/plugins/`
> (`<slug>_get_device_info`, `_discover_devices`, `_health_check` are `read`;
> `<slug>_execute_command` is `physical`). `home_assistant` keeps `grade: physical` as the
> server default, so a tool without its own entry asks every call. `tests/test_mcp_json_grades.py`
> fails if either server exposes a tool the file does not grade, or lists one it no longer
> exposes. **Adding a plugin means adding its four slugged entries to `mcp.json`.**

> **Planned (omni-jev, A4).** The bridge generates the git-ignored `.omni/mcp.local.json` from
> this file with Alfred's venv Python and the absolute repo root (J7).

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
