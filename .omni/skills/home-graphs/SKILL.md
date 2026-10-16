---
name: home-graphs
description: Alfred's workflow library — which graph to run for a multi-step job, and when a new one is actually warranted.
---

# Skill: home-graphs

Use this when a request needs more than one step, has a decision point, or needs
someone to approve something in the middle.

**The order is: look, run, and only then write.** `list_graphs` first — every time (a request that arrives through `front-door` is already routed; this is for jobs you are asked to start yourself). The
library below is what this house already knows how to do, and a workflow that exists
beats one you improvise, because it has been validated, its approval gate is already in
the right place, and it survives a restart. A job parked on somebody's approval
overnight is still there in the morning; a plan you were holding in your head is not.

## What is here

| Graph | Run it when |
|---|---|
| `front-door` | **Every conversation.** The host starts it for each new request; you do not start it with `run_graph`. A Jev (decide) node sorts the request, a second picks the verb for a device command, and a plain command runs with no chat model at all. Otherwise it hands the person to `device-help`, `chat`, `clarify`, or one of the three graphs below. Inputs: `message`, `earlier` (`""` if none), `continues` (`""` if none). |
| `home-routine` | A named routine touching several devices — morning, evening, away, movie. Inputs: `routine`, `earlier`. Reads current state, plans only the changes that are actually needed, takes **one** confirmation for the whole batch, applies, reads every device back, and a Jev `settled` check (LLM `verify-llm` as fallback) decides whether it took. |
| `device-troubleshoot` | Something is not responding or is behaving oddly. Inputs: **`report`** (the person's own words) and **`earlier`** — not `device`/`symptom`; `intake` works those out with the person. Then `inspect` gathers evidence, a Jev `triage` chooses recoverable / healthy / needs-human (LLM `diagnose-llm` as fallback), one bounded `recover`, a `read-back` from the source, and a Jev `fixed` check before anything is reported. |
| `new-capability` | The user wants something you cannot do. Input: `request`. Design, approve, build, verify, install, with a human gate before anything is built and another before anything is installed. This is the Forge. Its first job is checking the capability does not already exist, which is how it most often ends. |

## Conventions every graph follows

- **Who speaks.** A node speaks to the user iff it is `converse: true` or its id starts
  with `say-`. Everything else (inspect, recover, read-back, plan, apply...) is silent
  evidence-gathering, so the person hears one voice. Every terminal node must speak:
  name a closing node `say-...` (or make it converse) or the test suite rejects the graph.
- **`new-request` hands off.** A converse node that finds the person has moved on to a
  different job ends with the outcome `new-request` and puts the new request, in their
  words, as its output. The host starts a fresh `front-door` run with it. A graph that can
  hand off declares `new-request` among its terminal outcomes. A terminal node cannot
  also branch, so if an earlier node needs to hand off, route it to a closing `say-` node
  that emits `new-request` silently (see `say-bye` in `device-troubleshoot`).
- **Jev decides, an LLM backs it up.** A `decide` node uses `"provider": "typesafe",
  "model": "jev-latest"` in its `constraints`, and its `unsure`, `error` and `timeout`
  exits go to an agent node that makes the same judgement. Jev is for bounded either/or
  questions over text already on the blackboard; it cannot inspect anything.
- **`earlier`.** A node may declare `inputs.earlier` (a transcript the person chose to
  continue) only in a graph whose caller passes `earlier`.
- **Do not write house settings.** `cwd`, `mcp_enabled`, `mcp_config_path`, `on_ask` and
  `ask_timeout` belong to the host, which injects them. They never appear in a graph file.
- **Tools.** Narrow each agent node's `constraints.tools` to what it needs. Use `decide`
  to match a spoken description to an entity, and `readable: ["outputs.*"]` (with
  `run_context`) when a closing node must read whichever earlier outputs exist.

## When *not* to use one

A graph for a single action is bureaucracy. "Turn the kitchen light off" is one call.
"What's the temperature in here" is one call. If you can answer or act in one step, do
that — starting a run for it costs the user a slower answer and a run they now have on
the record for no reason.

## Writing a new one

`create_graph`, and only when you have looked and there genuinely is nothing that fits.
Then:

- **It must validate**, including the termination check. A workflow that might not
  finish is not a workflow, and `create_graph` will refuse to save one — that refusal is
  the feature.
- **Bound every cycle.** An edge that can loop needs `max_retries`. A troubleshooting
  loop against a device that is unplugged will otherwise run until a budget stops it.
- **Put the human gate before the irreversible part**, never after. The whole reason a
  physical routine is a graph rather than a sequence of prompts is that it asks once,
  in advance, showing what it is about to do.
- **Nodes are sessions, not turns.** A node gets a fresh context and only the blackboard
  paths its `inputs` name — so say what a node needs. A node that needs the earlier
  plan and does not name it will improvise one.
- **Write it to be reused.** It is going into the library. Parameterise the thing that
  varies (the routine name, the device) rather than hardcoding today's instance.
