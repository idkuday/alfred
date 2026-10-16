"""A fake omni client for the bridge client's tests (omni-jev plan A5): no daemon, no network.

``FakeKernel`` is a ``Journal`` (hand-built envelopes with gapless seq) whose ``add`` also
pushes to every attached stream, and which implements the slice of ``NativeClient`` the bridge
calls: ``attach``, ``start_run``, ``prompt``, ``respond``, ``list_runs``, ``cancel_run``,
``close``. The test scripts what "the daemon" does by assigning ``on_start`` / ``on_prompt`` /
``on_respond`` hooks that ``add`` more events, synchronously (inside the call) or later
(``loop.call_later``). ``FakeHost`` replaces ``GraphHost``: the bridge's policy half is the
kernel's, and tested there; here it only has to stay alive.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable

from omni.kernel.api import NotFoundError, RunInfo
from omni.kernel.events import Envelope, RunEnded, RunStarted
from omni.kernel.runs.fold import fold_run

from tests.omni_fixtures import Journal


class FakeStream:
    def __init__(self, kernel: "FakeKernel", stream_id: str, replay: list[Envelope]) -> None:
        self._kernel = kernel
        self.stream_id = stream_id
        self._queue: asyncio.Queue[Envelope | None] = asyncio.Queue()
        for env in replay:
            self._queue.put_nowait(env)
        self.end_reason: str | None = None
        self.resume_seq: int | None = None
        self.closed = False

    def push(self, env: Envelope) -> None:
        if not self.closed:
            self._queue.put_nowait(env)

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self) -> Envelope:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        self.closed = True
        self.end_reason = "closed"
        subs = self._kernel.subs.get(self.stream_id, [])
        if self in subs:
            subs.remove(self)
        self._queue.put_nowait(None)

    def end(self, reason: str) -> None:
        self.closed = True
        self.end_reason = reason
        self._queue.put_nowait(None)


class FakeKernel(Journal):
    def __init__(self) -> None:
        super().__init__()
        self.subs: dict[str, list[FakeStream]] = {}
        self.runs = 0
        self.started: list[dict[str, Any]] = []
        self.prompts: list[tuple[str, str]] = []
        self.responses: list[tuple[str, str, str, Any]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.attached: list[str] = []
        self.closed = False
        self.on_start: Callable[[str, dict[str, Any]], None] = lambda run_id, inputs: None
        self.on_prompt: Callable[[str, str], None] = lambda session_id, text: None
        self.on_respond: Callable[[str, str, str], None] = lambda stream, req, decision: None
        self.start_error: Exception | None = None
        self.list_error: Exception | None = None

    # -- Journal --
    def add(self, stream_id: str, event: Any, *, at: float | None = None) -> Envelope:
        env = super().add(stream_id, event, at=at)
        for sub in list(self.subs.get(stream_id, ())):
            sub.push(env)
        return env

    def stream(self, stream_id: str) -> None:
        """Make an (empty) stream exist, as a session does the moment a node is entered."""
        self.streams[stream_id]  # noqa: B018 - defaultdict creates it
        self.subs.setdefault(stream_id, [])

    # -- NativeClient slice --
    async def attach(self, stream_id: str, *, since: int = 0, deltas: bool = True) -> Any:
        if stream_id not in self.streams:
            raise NotFoundError(f"no stream {stream_id}")
        self.attached.append(stream_id)
        replay = [e for e in self.streams[stream_id] if e.seq > since]
        stream = FakeStream(self, stream_id, replay)
        self.subs.setdefault(stream_id, []).append(stream)
        return SimpleNamespace(head_seq=len(self.streams[stream_id]), stream_id=stream_id), stream

    async def start_run(self, module: str, definition_ref: str, inputs: Any, **kw: Any) -> RunInfo:
        if self.start_error is not None:
            raise self.start_error
        self.runs += 1
        run_id = f"run-{self.runs}"
        self.started.append({"run_id": run_id, "ref": definition_ref, "inputs": dict(inputs),
                             "module": module})
        self.add(run_id, RunStarted(run_id, module, definition_ref, dict(inputs)))
        self.on_start(run_id, dict(inputs))
        return RunInfo(run_id, run_id, module, "awaiting_decision", definition_ref)

    async def decide_run(self, *a: Any, **k: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("FakeHost does not decide")

    async def prompt(self, session_id: str, text: str, **kw: Any) -> Any:
        self.prompts.append((session_id, text))
        self.on_prompt(session_id, text)
        return SimpleNamespace(turn_id="t")

    async def respond(self, stream_id: str, req_id: str, decision: str, *, data: Any = None) -> Any:
        self.responses.append((stream_id, req_id, decision, data))
        self.on_respond(stream_id, req_id, decision)
        return SimpleNamespace()

    async def list_runs(self, *, state: str | None = None, module: str | None = None,
                        parent_session: str | None = None, include_ended: bool = False,
                        limit: int = 100) -> list[RunInfo]:
        if self.list_error is not None:
            raise self.list_error
        out = []
        for sid, envs in self.streams.items():
            if not sid.startswith("run-"):
                continue
            fold = fold_run(envs)
            if not fold.started or (fold.ended is not None and not include_ended):
                continue
            if module is not None and fold.module != module:
                continue
            out.append(RunInfo(sid, sid, fold.module, "completed" if fold.ended else "running",
                               fold.definition_ref, started_ts=fold.started_ts))
        return out[:limit]

    async def cancel_run(self, run_id: str, reason: str | None = None) -> Any:
        self.cancelled.append((run_id, reason))
        if fold_run(self.streams[run_id]).ended is None:
            self.add(run_id, RunEnded(run_id, "cancelled", reason))
        return SimpleNamespace()

    async def close(self) -> None:
        self.closed = True


class FakeHost:
    """Stands in for ``GraphHost``: alive until cancelled, decides nothing."""

    instances: list["FakeHost"] = []

    def __init__(self, client: Any, policy: Any, *, on_request: Any = None) -> None:
        self.client, self.policy, self.on_request = client, policy, on_request
        self.driven: list[str] = []
        FakeHost.instances.append(self)

    async def drive(self, run_id: str) -> None:
        self.driven.append(run_id)
        await asyncio.Event().wait()
