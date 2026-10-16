"""Hand-built kernel journals for the omni bridge's pure-logic tests (omni-jev plan A4).

Builds real ``omni.kernel.events`` envelopes (the type ``NativeClient`` streams yield) with
gapless per-stream ``seq`` numbers and a fake clock, plus two small graphs of our own that
have the *shape* of ``front-door`` and a troubleshooting subgraph (converse nodes, ``say-``
nodes, a ``new-request`` handoff) without depending on ``.omni/graphs/``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from omni.graph.schema import GraphLibrary
from omni.kernel.events import (
    BlockEnded,
    BlockKind,
    BlockStarted,
    CallEnded,
    CallOrigin,
    CallOutcome,
    CallStarted,
    Envelope,
    MessageEnded,
    MessageStarted,
    TurnCause,
    TurnEnded,
    TurnStarted,
)

FRONT_DOOR: dict[str, Any] = {
    "name": "front-door",
    "version": 1,
    "entry": "front",
    "terminal": ["say-act", "chat", "troubleshoot"],
    "nodes": [
        {"id": "front", "kind": "decide", "prompt": "route",
         "outcomes": ["device", "chat", "troubleshoot"],
         "constraints": {"provider": "typesafe", "model": "jev"}},
        {"id": "say-act", "kind": "tool", "capability": "mcp__home_assistant__apply",
         "args": {"target": "x"}},
        {"id": "chat", "kind": "agent", "converse": True, "prompt": "chat",
         "outcomes": ["done", "new-request"]},
        {"id": "ask-ok", "kind": "human", "request": {"prompt": "ok?"},
         "outcomes": {"approved": "go ahead", "rejected": "do not"}},
        {"id": "troubleshoot", "kind": "graph", "graph": "mini-trouble",
         "outcomes": ["done", "new-request"]},
    ],
    "edges": [
        {"from": "front", "to": "say-act", "on": "device"},
        {"from": "front", "to": "chat", "on": "chat"},
        {"from": "front", "to": "troubleshoot", "on": "troubleshoot"},
    ],
}

MINI_TROUBLE: dict[str, Any] = {
    "name": "mini-trouble",
    "version": 1,
    "entry": "intake",
    "terminal": ["say-report", "say-bye"],
    "nodes": [
        {"id": "intake", "kind": "agent", "converse": True, "prompt": "ask",
         "outcomes": ["identified", "new-request"]},
        {"id": "inspect", "kind": "agent", "prompt": "look", "outcomes": ["found"]},
        {"id": "say-report", "kind": "agent", "prompt": "report", "outcomes": ["done"]},
        {"id": "say-bye", "kind": "agent", "prompt": "bye", "outcomes": ["new-request"]},
        {"id": "inline", "kind": "graph", "graph": {
            "name": "inline-sub", "entry": "deep", "terminal": ["deep"],
            "nodes": [{"id": "deep", "kind": "agent", "prompt": "x", "outcomes": ["done"]}],
            "edges": []}},
    ],
    "edges": [
        {"from": "intake", "to": "inspect", "on": "identified"},
        {"from": "intake", "to": "say-bye", "on": "new-request"},
        {"from": "inspect", "to": "say-report", "on": "found"},
    ],
}


def library() -> GraphLibrary:
    lib = GraphLibrary()
    lib.load(MINI_TROUBLE)
    lib.load(FRONT_DOOR)
    return lib


class Journal:
    """Per-stream envelope lists with gapless seq and a clock that ticks 1 s per event."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.streams: dict[str, list[Envelope]] = defaultdict(list)
        self._n = 0

    def add(self, stream_id: str, event: Any, *, at: float | None = None) -> Envelope:
        self.now = at if at is not None else self.now + 1.0
        env = Envelope(
            stream_id=stream_id,
            seq=len(self.streams[stream_id]) + 1,
            ts=self.now,
            causation_id=None,
            trace_id=None,
            event=event,
        )
        self.streams[stream_id].append(env)
        return env

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    # ── session-stream helpers ──
    def message(self, stream_id: str, role: str, text: str) -> Envelope:
        msg = self._id("m")
        blk = self._id("b")
        self.add(stream_id, MessageStarted(msg, role))
        self.add(stream_id, BlockStarted(msg, blk, BlockKind.TEXT))
        env = self.add(stream_id, BlockEnded(msg, blk, text))
        self.add(stream_id, MessageEnded(msg))
        return env

    def tool_use_block(self, stream_id: str, content: str = "{}") -> Envelope:
        msg = self._id("m")
        blk = self._id("b")
        self.add(stream_id, MessageStarted(msg, "assistant"))
        self.add(stream_id, BlockStarted(msg, blk, BlockKind.TOOL_USE))
        env = self.add(stream_id, BlockEnded(msg, blk, content))
        self.add(stream_id, MessageEnded(msg))
        return env

    def turn(self, stream_id: str, *, end: bool = True) -> None:
        tid = self._id("t")
        self.add(stream_id, TurnStarted(tid, TurnCause.USER))
        if end:
            self.add(stream_id, TurnEnded(tid, "end_turn", {}))

    def call(
        self, stream_id: str, content: str, outcome: CallOutcome = CallOutcome.OK,
        name: str = "mcp__home_assistant__apply",
    ) -> Envelope:
        cid = self._id("c")
        self.add(stream_id, CallStarted(cid, name, {}, CallOrigin.MODULE))
        return self.add(stream_id, CallEnded(cid, outcome, content, 5))


# ── a troubleshooting conversation, told in steps ────────────────────────────

from omni.kernel.events import (  # noqa: E402
    FloorHeld,
    NodeEntered,
    NodeExited,
    Request,
    RequestKind,
    Response,
    RunEnded,
    RunStarted,
)


class Story:
    """run-1 (``front-door``) -> child run-2 (``mini-trouble``), one step per method.

    Streams: ``run-1``, ``run-2`` (run streams) and ``sess-intake`` / ``sess-inspect`` /
    ``sess-report`` / ``sess-bye`` (node sessions). ``child_runs`` and ``session_streams``
    give the shapes the bridge hands to the pure modules.
    """

    def __init__(self, message: str = "the hallway sensor keeps going offline") -> None:
        self.j = Journal()
        self.message = message

    # views
    @property
    def run1(self) -> list[Envelope]:
        return self.j.streams["run-1"]

    @property
    def run2(self) -> list[Envelope]:
        return self.j.streams["run-2"]

    @property
    def child_runs(self) -> dict[str, list[Envelope]]:
        return {"run-2": self.run2}

    @property
    def sessions(self) -> dict[str, list[Envelope]]:
        return {k: v for k, v in self.j.streams.items() if k.startswith("sess-")}

    # steps
    def start(self) -> "Story":
        j = self.j
        j.add("run-1", RunStarted("run-1", "graph", "front-door",
                                  {"message": self.message, "earlier": "", "continues": ""}))
        j.add("run-1", NodeEntered("run-1", "front", ""))
        j.add("run-1", NodeExited("run-1", "front", "troubleshoot"))
        j.add("run-1", NodeEntered("run-1", "troubleshoot", "run-2"))
        j.add("run-2", RunStarted("run-2", "graph", "mini-trouble", {"message": self.message}))
        return self

    def intake_asks(self, question: str = "Is it the motion sensor by the front door?") -> "Story":
        j = self.j
        j.add("run-2", NodeEntered("run-2", "intake", "sess-intake"))
        j.message("sess-intake", "user", "SEED: you are the intake agent")
        j.add("sess-intake", TurnStarted("t-seed", TurnCause.USER))
        j.tool_use_block("sess-intake", '{"name":"get_state"}')
        j.message("sess-intake", "assistant", question)
        j.add("sess-intake", TurnEnded("t-seed", "end_turn", {}))
        j.add("run-2", FloorHeld("run-2", "intake", "sess-intake"))
        return self

    def user_replies(self, text: str = "yes, the zigbee one", reply: str | None = None) -> "Story":
        """The person speaks to the held floor; with ``reply`` the node's turn also ends."""
        j = self.j
        j.message("sess-intake", "user", text)
        j.add("sess-intake", TurnStarted("t-next", TurnCause.USER))
        if reply is not None:
            j.message("sess-intake", "assistant", reply)
            j.add("sess-intake", TurnEnded("t-next", "end_turn", {}))
        return self

    def intake_exits(self, outcome: str = "identified",
                     output: str = "binary_sensor.hallway_motion: offline daily") -> "Story":
        j = self.j
        env = j.call("sess-intake", output, name="emit_outcome")
        j.add("run-2", NodeExited("run-2", "intake", outcome, f"sess-intake#{env.seq}"))
        return self

    def inspect(self, notes: str = "INTERNAL: zigbee2mqtt shows 3 reconnects") -> "Story":
        j = self.j
        j.add("run-2", NodeEntered("run-2", "inspect", "sess-inspect"))
        j.message("sess-inspect", "assistant", notes)
        j.add("run-2", NodeExited("run-2", "inspect", "found"))
        return self

    def ask_permission(self, req_id: str = "req-1") -> "Story":
        self.j.add("run-2", Request(req_id, RequestKind.PERMISSION,
                                    {"tool": "mcp__ha__reload_integration", "args": {},
                                     "options": ["once", "deny"]}))
        return self

    def answer(self, decision: str = "once", req_id: str = "req-1") -> "Story":
        self.j.add("run-2", Response(req_id, "alfred", decision, "bridge"))
        return self

    def report(self, text: str = "It was the Zigbee integration.", *, end: bool = True) -> "Story":
        j = self.j
        j.add("run-2", NodeEntered("run-2", "say-report", "sess-report"))
        j.message("sess-report", "assistant", text)
        if end:
            env = j.streams["sess-report"][-2]  # the BlockEnded
            j.add("run-2", NodeExited("run-2", "say-report", "done", f"sess-report#{env.seq}"))
            j.add("run-2", RunEnded("run-2", "completed"))
            j.add("run-1", NodeExited("run-1", "troubleshoot", "done", f"sess-report#{env.seq}"))
            j.add("run-1", RunEnded("run-1", "completed"))
        return self

    def bye(self, output: str = "turn off the kitchen light", spoken: str = "Sure, switching.") -> "Story":
        """intake emits new-request, say-bye emits new-request (silent), both runs end."""
        j = self.j
        env = j.call("sess-intake", output, name="emit_outcome")
        j.add("run-2", NodeExited("run-2", "intake", "new-request", f"sess-intake#{env.seq}"))
        j.add("run-2", NodeEntered("run-2", "say-bye", "sess-bye"))
        j.message("sess-bye", "assistant", spoken)
        env2 = j.call("sess-bye", output, name="emit_outcome")
        j.add("run-2", NodeExited("run-2", "say-bye", "new-request", f"sess-bye#{env2.seq}"))
        j.add("run-2", RunEnded("run-2", "completed"))
        j.add("run-1", NodeExited("run-1", "troubleshoot", "new-request", f"sess-bye#{env2.seq}"))
        j.add("run-1", RunEnded("run-1", "completed"))
        return self
