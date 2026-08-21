---
name: home-graphs
description: Alfred's workflow library — which graph to run for a multi-step job, and when a new one is actually warranted.
---

# Skill: home-graphs

Use this when a request needs more than one step, has a decision point, or needs
someone to approve something in the middle.

**The order is: look, run, and only then write.** `list_graphs` first — every time. The
library below is what this house already knows how to do, and a workflow that exists
beats one you improvise, because it has been validated, its approval gate is already in
the right place, and it survives a restart. A job parked on somebody's approval
overnight is still there in the morning; a plan you were holding in your head is not.

## What is here

| Graph | Run it when |
|---|---|
| `home-routine` | A named routine touching several devices — morning, evening, away, movie. Reads current state, plans only the changes that are actually needed, takes **one** confirmation for the whole batch, applies, then reads back to check it took. |
| `device-troubleshoot` | Something is not responding or is behaving oddly. Separates *unreachable* from *reachable but in an unexpected state* from *nothing is wrong and the request was ambiguous* — three cases that look identical from the outside. One bounded recovery attempt, then either a confirmation it is fixed or something specific the user can act on. |
| `new-capability` | The user wants something you cannot do. Design → approve → build → verify → install, with a human gate before anything is built and another before anything is installed. This is the Forge. Its first job is checking the capability does not already exist, which is how it most often ends. |

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
