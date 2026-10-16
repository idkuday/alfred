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

> **As built (omni-jev, A1).** `REPO_ROOT = Path(__file__).resolve().parents[1]` is a
> module constant in `config.py`, not a setting. `plugins_dir` defaults to the absolute
> `ai_server/plugins`, and `PluginManager()` defaults to its own package directory, so
> plugins load from any cwd (before A1 a relative `"plugins"` found none outside the
> repo root). Every setting below is overridable by env var of the same name, upper-cased
> (`JEV_KEY_FILE`, `ALFRED_BACKEND`, ...):
>
> - `jev_key_file` (default `<repo>/jev.env`): the Jev key file. Raw key, no `KEY=`
>   prefix. Code reads it only through `KeyFileCredential`. Never print or commit it.
> - `decision_provider` (`"typesafe"`), `decision_model` (`"jev-latest"`).
> - `resolve_threshold` (`0.6`), `resolve_max_candidates` (`200`).
> - `alfred_backend` (`"omni"` or `"core"`, default `"omni"`).
> - `omni_front_graph` (`"front-door"`), `omni_node_provider`, `omni_node_model` (both
>   `None` (unset).
> - `omni_ask_timeout` (`300.0` s), `omni_turn_timeout` (`120.0` s),
>   `omni_earlier_chars` (`6000`).
> - `data_dir` (default `<repo>/data`, git-ignored).
>
> These are declared only. No code reads them yet; later omni-jev tasks wire them up.
> `.gitignore` covers `jev.env`, `.omni/mcp.local.json` and `data/`. See
> [omni-jev-plan.md](omni-jev-plan.md) §10.1.

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
