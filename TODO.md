# Project TODOs

## AlfredCore — Replace Router + QA with Single LLM Call

**Goal**: Merge router and QA handler into `AlfredCore` — one model that both decides and responds.

**Why**: Currently every Q&A request makes two LLM calls (router → QA) to the same model. Core does it in one.

### Design Decisions

| Decision | Choice |
|----------|--------|
| Output strategy | JSON for tool calls, plain text for conversation |
| Tool call format | Keep current `call_tool` and `propose_new_tool`, drop `route_to_qa` |
| `intent_processor` | Keep — Core can select it as a tool |
| `propose_new_tool` | Keep — JSON output type for Forge |
| Tool results | We format (no second LLM call) |
| Malformed JSON | One retry with minimal fix-it prompt (no context re-sent) |
| Endpoints | No changes — `/execute` API contract stays the same |
| Migration | Feature flag `ALFRED_MODE=core` vs `router`, build alongside |

### Output Parsing
- Output starts with `{` and is valid tool call JSON → dispatch to integration
- Output is plain text → return as conversational response
- Output starts with `{` but malformed → one retry with minimal prompt: "Fix this JSON: {broken output}"
- Retry also fails → return error

### Implementation
- [x] New module `ai_server/core/` with `AlfredCore` class
- [x] Core prompt: personality + tools + conversation context + instructions (respond OR tool JSON)
- [x] Retry prompt: minimal JSON fix-it prompt (no conversation context)
- [x] Output parser: detect JSON vs plain text, validate tool calls
- [x] Update `main.py`: feature flag to switch between Core and Router
- [x] Config: `ALFRED_CORE_MODEL`, `ALFRED_MODE` setting
- [x] Unit tests for Core (mock LLM)
- [ ] Integration test: compare Core vs Router on standard test cases
- [ ] Once validated, deprecate `alfred_router/router.py` and `alfred_router/qa_handler.py`

### What Gets Replaced
- `alfred_router/router.py` → `core/core.py`
- `alfred_router/qa_handler.py` → absorbed into Core
- `alfred_router/schemas.py` → simplified (drop `RouteToQADecision`)
- `alfred_router/prompts/router.txt` + `qa.txt` → single Core prompt

### What Stays
- `alfred_router/tool_registry.py` — Core still needs tool list
- `intent_processor.py` — Core can still select it
- All endpoints — API contract unchanged
- Session memory, TTS, STT — untouched

---

## Session Memory Compaction

**Goal**: When messages exceed the history limit, summarize older messages instead of dropping them.

### Design
- When message count crosses threshold (e.g., 10), summarize oldest messages via LLM
- Store summary in DB (special `role: "summary"` message or `summary` column on sessions table)
- Context becomes: `[stored summary] + [last N raw messages]`
- Summarization happens once on threshold, not every request
- Can be async (after response sent) to avoid adding latency
- `SummaryProvider` stub in `context.py` already exists for this

### Implementation
- [ ] Design summary storage (summary message vs sessions column)
- [ ] Implement compaction trigger (after save, check count)
- [ ] LLM summarization call (background/async)
- [ ] Update `ContextProvider` to combine summary + recent messages
- [ ] Tests

---

## Tool Result Formatting — Hybrid (Future)

- [ ] For complex tool results (device lists, status queries), let Core format the response via a second LLM call
- [ ] Simple success/fail results stay template-formatted (no second call)

---

## Dead Code Cleanup
- [ ] **Remove `/voice-command` endpoint and related code** — frontend uses `/transcribe` + `/execute` flow instead; the combined endpoint is unused
  - Backend: `main.py` (endpoint), `models.py` (`VoiceCommandResponse`)
  - Frontend: `api.js` (`voiceCommand()`), `VoiceButton.jsx` (`useVoiceCommandEndpoint`/`onVoiceCommand` props), related tests

## The Forge (Agent System)
- [ ] **Publisher Node Improvements**:
    - Check for plugin name conflicts before writing to disk
    - Validate filenames ensuring they use valid characters and conventions (snake_case)
    - Handle versioning if a plugin already exists

## Frontend Enhancements
- [ ] Rich response display (device state cards, tool proposals)
- [ ] Settings panel
- [ ] Command suggestions / quick actions
- [ ] Device management sidebar

## Infrastructure
- [ ] CORS lockdown for production
- [ ] Authentication
- [ ] Rate limiting
- [ ] WebSocket/SSE streaming
- [ ] Plugin hot-reload
