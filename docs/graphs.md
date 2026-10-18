# Alfred's graphs

`.omni/graphs/` — JSON documents run by omni-kernel's graph walker (graph-spec). Every
node declares its own context (`inputs`); nothing else reaches it.

> **As built (omni-jev, A3).** Four graphs: `front-door` (the conversation), and three
> subgraphs it runs — `device-troubleshoot`, `home-routine`, `new-capability`.

## Conventions

- **Who speaks (J8).** A node speaks to the user iff it is `converse: true` or its id
  starts with `say-`. A speaking agent contributes its assistant text; a speaking tool
  node contributes its output text. Every terminal node of every graph speaks (a `graph`
  node is terminal by way of its subgraph's terminals). Evidence-gathering agents
  (`inspect`, `recover`, `read-back`, `plan`, `apply`...) are silent.
- **Handoff (J10).** A converse node's `new-request` outcome ends the run; its output is
  the new request, verbatim. The bridge starts a fresh `front-door` run with that as
  `inputs.message`.
- **Run inputs (J12).** `front-door` always receives `message`, `earlier` (`""` if none)
  and `continues` (`""` if none). Subgraphs receive only what their `graph` node passes;
  a node that declares `inputs.earlier` is in a graph whose caller passes `earlier`.
  Running `device-troubleshoot` or `home-routine` directly therefore needs `earlier`
  supplied (an empty string is fine).
- **Jev decide nodes (J16).** Every `decide` node names `provider: typesafe`,
  `model: jev-latest`, and routes `unsure`, `error` and `timeout` to an LLM agent
  fallback. Without a Typesafe key every decide node exits `error`, so each graph
  degrades to its LLM path instead of failing.
- **House constraints (J6).** `cwd`, `mcp_enabled`, `mcp_config_path`, `on_ask` and
  `ask_timeout` are never written in a graph file; the bridge injects them on load.
  `tests/test_graphs.py` fails a file that carries one.
- **Agent `alfred` (J17).** `agent.yml` sets `decision: {provider: typesafe, model:
  jev-latest}` and allows `decide` and `run_context`. Nodes narrow `tools` to what they
  need; only `new-capability`'s designer chain touches graph listing.
  > **As built (pr-review, A1).** The four manifests (`alfred`, `alfred-builder`,
  > `alfred-designer`, `alfred-verifier`) name `claude-sonnet-5-5` and set no `max_tokens`
  > or `max_iterations` (kernel defaults: the per-model output cap, no iteration cap). Each
  > keeps a `budget.cost` (50.00 / 1.50 / 0.50 / 0.75). `alfred` also drops `budget.tokens`,
  > `mode` and `time_aware` (the kernel defaults, `interactive` and `basic`, are what it
  > wants) and compacts at 150 000 tokens keeping 6 recent turns.
  > `tests/test_agent_manifests.py` pins all of this.

Validate with `omni.graph.validate.validate(graph, library=discover_library(repo))`
(what `tests/test_graphs.py` does). The `omni graph validate` CLI builds a library of one
file, so it reports `front-door`'s `graph` nodes as unresolved; use the Python call for
that file. The validator does not compare a `graph` node's declared `outcomes` with the
subgraph's terminal outcomes; `tests/test_graphs.py` does.

## front-door

The conversation graph (J1: one conversation is one run). Terminals: `say-act`,
`say-status`, `device-help`, `chat`, `clarify`, `troubleshoot`, `routine`,
`new-capability`.

| Node | Kind | What it does | Leaves by |
|---|---|---|---|
| `front` | decide (Jev, threshold 0.6) | Classifies `inputs.message`: device, troubleshoot, routine, new-capability, chat. | each outcome to its node; `unsure`/`error`/`timeout` to `clarify` |
| `device-action` | decide (Jev, 0.6) | Picks the verb: turn-on, turn-off, toggle, status, other. | on/off/toggle to `resolve`; `status` to `resolve-read`; `other`/`unsure`/`error`/`timeout` to `device-help` |
| `resolve` | tool `mcp__home_assistant__resolve_entity` | Resolves the described device for a write (request + verb). Jev runs inside the MCP server. | `resolved` to `say-act`; `ambiguous`/`none`/`error` to `device-help` |
| `resolve-read` | tool, same | Same, with `action: status`. | `resolved` to `say-status`; others to `device-help` |
| `say-act` | tool `mcp__home_assistant__apply` | Performs the action on the device `resolve` found, passing `{{ outputs.resolve.entity_id }}` and `{{ outputs.resolve.entity_name }}` (pr-review A3). Physical, so it asks first and the prompt shows `entity_name`. Terminal; an `error` ends the run (the bridge words it). | terminal |
| `say-status` | tool `mcp__home_assistant__describe` | Reads `{{ outputs.resolve-read.entity_id }}` / `.entity_name` and states it. Terminal. | terminal |
| `device-help` | agent, converse (15m) | The LLM device path: `list_entities`, `decide` to match a description, then the HA tools; asks when the device is unclear. Tools: mcp, decide, clock, remember. | `done`; `new-request` |
| `chat` | agent, converse (30m) | Plain conversation. Does nothing in the house; hands requests back with `new-request`. Tools: clock, remember. | `done`; `new-request` |
| `clarify` | agent, converse (10m) | One short question when routing was unsure. Tools: clock. | `new-request` (restated clearly); `done` |
| `troubleshoot` | graph `device-troubleshoot` | Passes `report` = message, `earlier`. | outcomes `done`, `new-request` |
| `routine` | graph `home-routine` | Passes `routine` = message, `earlier`. | outcome `done` |
| `new-capability` | graph `new-capability` | Passes `request` = message. | outcome `done` |

The LLM-free path is `front` -> `device-action` -> `resolve` -> `say-act` (or
`resolve-read` -> `say-status`): two Jev calls plus the MCP server's resolver, no chat model.

## device-troubleshoot

Inputs `report` (the person's words) and `earlier`. Terminals: `intake` (which also
routes `identified` onward), `say-report`, `say-escalate`; subgraph outcomes `done`, `new-request`.

| Node | Kind | What it does | Leaves by |
|---|---|---|---|
| `intake` | agent, converse (15m) | Finds the device and symptom with the person, reading state before asking. Tools: mcp, decide, clock. | terminal; `identified` to `inspect`, else it ends the run with `done` (a one-line goodbye) or `new-request` (the new request as its output, handed to a fresh `front-door` run) |
| `inspect` | agent | Gathers evidence: state, timestamps, integration reachability. Changes nothing. Tools: mcp, clock. | `done` to `triage` |
| `triage` | decide (Jev, 0.7) | recoverable / healthy / needs-human. | `recoverable` to `recover`; `healthy` to `say-report`; `needs-human` to `say-escalate`; `unsure`/`error`/`timeout` to `diagnose-llm` |
| `diagnose-llm` | agent | The same three-way judgement by an LLM. | same three targets |
| `recover` | agent | Exactly one recovery attempt (physical actions ask). Tools: mcp, clock. | `recovered` to `read-back`; `still-broken` to `say-escalate` |
| `read-back` | agent | Reads the device back from the source; success of the call is not evidence. | `done` to `fixed` |
| `fixed` | decide (Jev, 0.7) | Is the symptom gone per the read-back? | `fixed` to `say-report`; `not-fixed` back to `recover` (`max_retries: 1`); `unsure`/`error`/`timeout` to `confirm-llm` |
| `confirm-llm` | agent | The same judgement by an LLM. | `fixed` to `say-report`; `not-fixed` back to `recover` (`max_retries: 1`) |
| `say-report` | agent (speaks) | Says what was wrong and what was done; `run_context` reads `outputs.recover` and `outputs.read-back` if they ran (`readable: ["outputs.*"]`). | terminal, `done` |
| `say-escalate` | agent (speaks) | The shortest specific physical step for the person, and what was tried. | terminal, `done` |

> **As built (pr-review, A2).** `say-bye` is gone: `intake` is itself a terminal node and routes
> only `identified` (kernel P18, AD8), so a hand-off costs one LLM call fewer. `gave-up` is
> renamed `done`, so the graph's terminal outcomes stay `done` and `new-request` and front-door's
> `troubleshoot` node is unchanged. The bridge still keeps a terminal speaker's words out of the
> reply when it exits `new-request`. An idle `intake` (15 minutes) now ends the run with
> `timeout` instead of failing it with "no matching edge".

## home-routine

Inputs `routine` and `earlier`. Terminals: `say-report`, `say-cancelled`; outcome `done`.

| Node | Kind | What it does | Leaves by |
|---|---|---|---|
| `plan` | agent | Reads current state of every device the routine touches and lists only the needed changes. Tools: mcp, decide, clock. | `ready` to `confirm`; `nothing-to-do` and `blocked` to `say-cancelled` |
| `confirm` | human | One approval for the whole batch (shows `inputs.routine`, `outputs.plan`); 10m, times out to `rejected`. The bridge surfaces it. | `approved` to `apply`; `rejected` to `say-cancelled` |
| `apply` | agent | Applies exactly the plan; each physical call still asks. Tools: mcp, clock. | `applied`/`partial` to `read-back` |
| `read-back` | agent | Reads every planned device back and reports intended against actual. | `done` to `settled` |
| `settled` | decide (Jev, 0.7) | Does the house now match the plan? | `settled`/`drifted` to `say-report`; `unsure`/`error`/`timeout` to `verify-llm` |
| `verify-llm` | agent | The old `verify` judgement by an LLM, over plan, apply and read-back. | `settled`/`drifted` to `say-report` |
| `say-report` | agent (speaks) | One or two sentences on what happened; `readable: ["outputs.*"]` reaches whichever of `read-back`/`settled`/`verify-llm` ran. | terminal, `done` |
| `say-cancelled` | agent (speaks) | Says the routine was not applied. | terminal, `done` |

## new-capability

`design` -> `approve` (human) -> `build` -> `verify` -> `review` (human) -> `say-installed`;
`say-declined`. Input `request`; outcome `done`. Per J20 this graph keeps its LLM `verify`;
the only change is that `installed` and `declined` are renamed `say-installed` and
`say-declined` (J8). `design` (agent `alfred-designer`) first checks the capability does not
already exist; `build` and `verify` run as `alfred-builder` and `alfred-verifier` with those
agents' own tool lists; both human gates are surfaced by the bridge. Verification failures
and revisions loop back to `build` with `max_retries: 2`.
