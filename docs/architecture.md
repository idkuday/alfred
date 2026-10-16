# Alfred — architecture

How Alfred is built today. Running on Omni is in [omni.md](omni.md); the graphs node by
node are in [graphs.md](graphs.md); the plan that is moving Alfred onto Omni + Jev is
[omni-jev-plan.md](omni-jev-plan.md).

> The root `ARCHITECTURE.md` and `CODEBASE_SUMMARY.md` describe the older AlfredRouter /
> Q&A-handler design and are stale.

## Shape

```
alfred-ui / voice ──HTTP──► FastAPI (ai_server/main.py)
                               │
                 today:        ├── AlfredCore (ai_server/core/) — one Ollama call per request
                               │      └── CallToolDecision → HomeAssistantIntegration / plugins
                               ├── SessionStore (ai_server/memory/, SQLite)
                               ├── Transcriber (Whisper) · Synthesizer (Piper)
                               └── /devices → HomeAssistantIntegration

  MCP servers (ai_server/mcp/) — Home Assistant and the plugins, for Omni (.omni/mcp.json)
  .omni/ — the `alfred` agent definition, three graphs, one skill
```

> **Planned (omni-jev, A5/A6/A9).** `/execute` and `/sessions` are served by the Omni
> bridge (`ai_server/omni_bridge/`): each conversation is one run of the `front-door`
> graph, hosted in-process by `GraphHost`, nodes executed by `omnid`. AlfredCore, the
> intent processor, `alfred_router/` and the SQLite session store are deleted (J15).
> Whisper, Piper and `/devices` are unchanged.

## Configuration

`ai_server/config.py` (`Settings`, pydantic-settings, reads `.env` from the working
directory).

> **Planned (omni-jev, A1).** `REPO_ROOT` is a module constant; `plugins_dir` defaults to
> the absolute `ai_server/plugins` (so plugins load from any cwd); new settings
> `jev_key_file`, `decision_provider`, `decision_model`, `resolve_threshold`,
> `resolve_max_candidates`, `alfred_backend`, `omni_front_graph`, `omni_node_provider`,
> `omni_node_model`, `omni_ask_timeout`, `omni_turn_timeout`, `omni_earlier_chars`,
> `data_dir` — see [omni-jev-plan.md](omni-jev-plan.md) §10.1.

## HTTP API

Today: `POST /execute {user_input, session_id?, voice_mode}`, `POST /transcribe`,
`POST /synthesize`, `GET /devices`, `GET /devices/{entity_id}`, `GET/POST /sessions`,
`GET/DELETE /sessions/{id}`, `GET /health`.

> **Planned (omni-jev, A6).** Same routes and shapes, served by the bridge, plus
> `POST /sessions/{id}/continue` (load an earlier conversation) and
> `POST /sessions/{id}/asks/{req_id}` (answer a pending ask). `/execute` responses gain
> `conversation_state`, `pending`, `new_conversation`, `continued_from`. See
> [omni-jev-plan.md](omni-jev-plan.md) §8.

## Tests

`alfred\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` from the repo
root.
