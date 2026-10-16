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

> **As built (omni-jev, A6).** `main.py` serves `/execute` and `/sessions` from one of two
> brains, chosen at start-up by `settings.alfred_backend`:
>
> - `omni` (default): the lifespan builds `AlfredOmni(REPO_ROOT, settings)` (imported
>   lazily from `ai_server.omni_bridge`, so Alfred imports without `omni-kernel`), awaits
>   `start()`, and keeps it in the module global `alfred_omni`. If the import, the
>   constructor or `start()` fails it is logged and the server keeps serving: the
>   bridge routes answer 503 until a later call reconnects (`say()` retries `start()` once,
>   and an instance that constructed is kept for that). Shutdown awaits `close()` and calls
>   `decisions.close()`. In this mode AlfredCore (Ollama) and the SQLite session store are
>   not initialised.
> - `core`: the previous path, unchanged (AlfredCore, `SessionStore`).
>
> Whisper, Piper and `/devices` do not depend on the backend.

> **Planned (omni-jev, A5/A9).** `AlfredOmni` itself (`omni_bridge/client.py`: each
> conversation is one run of the `front-door` graph, hosted in-process by `GraphHost`,
> nodes executed by `omnid`) lands with A5. A9, deferred, would delete AlfredCore, the
> intent processor, `alfred_router/` and the SQLite session store (J15).

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

Unchanged for `alfred-ui`: `POST /execute {user_input, session_id?, voice_mode}`,
`POST /transcribe`, `POST /synthesize`, `GET /devices`, `GET /devices/{entity_id}`,
`GET/POST /sessions`, `GET/DELETE /sessions/{id}`, `GET /health`.

> **As built (omni-jev, A6).** With `alfred_backend = "omni"` the routes are served by
> `AlfredOmni` (plan §7.2); with `"core"` they behave as before. A `session_id` is a
> front-door run id (J1).
>
> | Route | Omni backend |
> |---|---|
> | `POST /execute` | `{intent: "conversation", answer, message (= answer), session_id, conversation_state, pending, new_conversation, continued_from, audio_base64?}`. `session_id` is the reply's `conversation_id`, which is a NEW id after a handoff or a fresh start; keep using the one returned. `conversation_state` is `awaiting_user`, `awaiting_approval`, `working` or `ended`. `pending` is the ask being put to the person (`req_id`, `kind`, `question`, `options`, ...) or `null`. `audio_base64` is present when `voice_mode` is true, a synthesizer is loaded, and the answer is not empty; synthesis failures never fail the request. |
> | `GET /sessions` | `{count, sessions}`; each session is a conversation summary with `session_id` (= `conversation_id`), `title`, `started_at` (also as `created_at`), `last_active`, `state`, `continued_from`. |
> | `POST /sessions` | `{session_id: "pending-<uuid4>"}`, a placeholder: the first `/execute` with it starts the run and returns the real id. |
> | `GET /sessions/{id}` | `{session, messages}`; each message has `role`, `text`, `node`, `at`, plus `content` and `timestamp` (the names `alfred-ui` reads). |
> | `DELETE /sessions/{id}` | Cancels the run if live and hides it; 404 if unknown. |
> | `POST /sessions/{id}/continue` | `{user_input, voice_mode?}`; loads an earlier conversation into a NEW run (J11). Same reply shape as `/execute`. |
> | `POST /sessions/{id}/asks/{req_id}` | `{decision}`; answers a pending ask directly (J13). Same reply shape as `/execute`; 409 if `req_id` is not pending. |
> | `GET /health` | Adds `omni: "connected" \| "unavailable"` (the daemon answers); `alfred_backend` is `"omni"` or `"core"` (with `core`, `alfred_core: "ready" \| "unavailable"` carries what `alfred_backend` used to say); `status` is `degraded` unless Home Assistant and the bridge are both up. |
>
> Errors: `ConversationNotFound` (a `LookupError`) maps to 404; `BridgeUnavailable` (a
> `RuntimeError`) to 503, as does a bridge that never constructed; a `ValueError` from
> the bridge maps to 400 (409 on the ask route). Any other exception is a 500. Under the
> `core` backend `/sessions/{id}/continue` and `/sessions/{id}/asks/{req_id}` answer 404.
> Request bodies for the two new routes are strict (`extra="forbid"`, 422 on unknown
> fields). Tests: `tests/test_api_omni.py` (a fake `AlfredOmni` injected into
> `ai_server.main.alfred_omni`). See [omni-jev-plan.md](omni-jev-plan.md) §8.

## Tests

`alfred\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` from the repo
root.
