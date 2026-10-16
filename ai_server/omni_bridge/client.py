"""``AlfredOmni``: Alfred's conversations as omni-kernel graph runs (omni-jev plan sections 7.2-7.9).

One conversation is one run of the ``front-door`` graph (J1). This module is the thin I/O loop
around the pure modules of ``ai_server/omni_bridge/``: it owns the connection, a ``GraphHost``
per run (the graph policy lives in this process, J2), and one *mirror* per conversation - the
envelopes of the run's stream, its child runs' streams and its speaking nodes' session streams,
each read continuously by its own task (``NativeClient`` stalls the whole connection if a stream
queue fills, ``omni/codec/client.py`` docstring item 2). Everything the bridge then says is a
pure function of that mirror, which is why a restart loses nothing (J18): a new mirror replays
the same journal.

``say()`` submits work (start a run, answer an ask, prompt the held floor) and waits for
*quiescence* (section 7.4) up to ``settings.omni_turn_timeout`` (J19). Details that are easy to
get wrong, and why the code is shaped as it is:

* **Replay before judging.** A stream the bridge attaches late replays history. Before a reply
  is composed the mirror must have consumed every attached stream up to the head it had at
  attach time, and every pending attach must have finished (``_Mirror.synced``), or the last
  node's words would be missing from a run that ended in milliseconds.
* **Submission marks.** Quiescence ignores turns that started before the work was submitted
  (``state_of(since=...)``); the words already said are tracked separately, as "delivered"
  marks, so speech that arrived after a ``working`` timeout is delivered on the next call
  instead of lost.
* **Answers are waited for.** After ``respond`` the mirror still shows the old pending
  ``Request`` until the ``Response`` is read back, so the wait ignores quiescence until the
  mirror has seen it.
* **Decision words.** ``allow`` goes to the kernel as ``once`` (A4, ``asks.decision_word``).

Imports ``omni`` at module level like the other bridge modules; the package ``__init__``
resolves ``AlfredOmni`` lazily so the rest of Alfred does not need omni to import.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omni.codec.client import REASON_DISCONNECTED, NativeClient
from omni.codec.discovery import connect as connect_transport
from omni.codec.discovery import probe, read_discovery
from omni.graph.host import GraphHost, start_graph_run
from omni.graph.policy import GRAPH_MODULE, GraphPolicy
from omni.graph.schema import Graph, GraphError, GraphLibrary, Node
from omni.kernel.api import PROTOCOL_VERSION, ConflictError, NotFoundError, UnavailableError
from omni.kernel.events import (
    Envelope,
    NodeEntered,
    Request,
    RunStarted,
)
from omni.kernel.runs.fold import RunFold, fold_run

from .. import decisions
from ..config import Settings
from . import asks, library as library_mod, quiescence, speakers, transcript as transcript_mod
from .models import (
    BridgeUnavailable,
    ConversationNotFound,
    ConversationReply,
    ConversationState,
    ConversationSummary,
    PendingAsk,
    Transcript,
    hidden_path,
    parse_hidden,
    parse_runs,
    render_hidden,
    render_runs,
    runs_path,
    with_hidden,
)

logger = logging.getLogger(__name__)

__all__ = ["AlfredOmni", "BridgeUnavailable", "ConversationNotFound"]

#: What Alfred says when a turn is still running and the person speaks again (section 7.8).
STILL_ON_IT = "I'm still on it."
#: ... when the run died without saying anything.
WENT_WRONG = "Sorry — something went wrong on my side."

#: Seconds to wait for a mirror to finish replaying before judging it (adoption, routing).
SYNC_WAIT = 10.0
#: How many of the newest runs ``list_conversations`` looks at, and how many it returns.
LIST_SCAN = 200
LIST_MAX = 100
#: Ended runs never change, so their folds are cached (bounded).
ENDED_CACHE_MAX = 512
#: Concurrent stream peeks while listing.
PEEK_PARALLEL = 8

Decide = Callable[..., Awaitable[tuple[str | None, float]]]


async def _join_running_daemon() -> Any:
    """A ``NativeClient`` on an already-running daemon, using only ``omni.codec``.

    The fallback for a venv without the ``omni`` CLI extra: same handshake as
    ``omni.cli.connect.open_client`` (token from the discovery file), but it cannot start a
    daemon, so none running is ``UnavailableError``.
    """
    record = read_discovery()
    if record is None or not record.serves(PROTOCOL_VERSION) or not await probe(record):
        raise UnavailableError(
            "no omni daemon is running (start `omnid`, or install omni-harness[cli] so the "
            "bridge can start one)"
        )
    reader, writer = await connect_transport(record)

    async def release() -> None:
        with contextlib.suppress(Exception):
            writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 5.0)

    return await NativeClient.connect(
        reader, writer, client="alfred", token=record.token, on_closed=release
    )


# ── the mirror: everything read of one conversation ──────────────────────────


class _Mirror:
    """The envelopes of one conversation's streams, kept current by reader tasks.

    ``envs`` maps stream id to its envelopes: the root run (``runs[0]``), its child runs and
    the node sessions that can speak (J8) - decide, human and silent agent nodes are never
    attached. Streams are discovered from ``NodeEntered`` on a run stream, whose
    ``session_id`` is a child run id for a ``graph`` node (kernel ``_run_subrun``).
    """

    def __init__(self, client: Any, library: GraphLibrary) -> None:
        self._client = client
        self._library = library
        self.root = ""
        self.runs: list[str] = []
        self.envs: dict[str, list[Envelope]] = {}
        self.graphs: dict[str, Graph | None] = {}
        self.head: dict[str, int] = {}
        self.failed: set[str] = set()
        self.dead = False
        self.changed = asyncio.Event()
        self._want: set[str] = set()
        self._streams: dict[str, Any] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- lifecycle --

    async def open(self, root: str) -> None:
        """Attach the root run's stream (raises ``NotFoundError`` for an unknown id)."""
        self.root = root
        self.runs = [root]
        self._want.add(root)
        self.envs[root] = []
        handle, stream = await self._client.attach(root, since=0, deltas=False)
        self._begin_pump(root, handle, stream)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for stream in list(self._streams.values()):
            with contextlib.suppress(Exception):
                await stream.aclose()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._streams.clear()

    def notify(self) -> None:
        self.changed.set()

    # -- discovery and reading --

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _want_stream(self, stream_id: str, *, is_run: bool) -> None:
        if stream_id in self._want:
            return
        self._want.add(stream_id)
        self.envs[stream_id] = []
        if is_run:
            self.runs.append(stream_id)
        self._spawn(self._attach_and_pump(stream_id))

    async def _attach_and_pump(self, stream_id: str) -> None:
        try:
            handle, stream = await self._client.attach(stream_id, since=0, deltas=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("cannot attach %s: %s", stream_id, type(exc).__name__)
            self.failed.add(stream_id)
            self.notify()
            return
        self._begin_pump(stream_id, handle, stream)

    def _begin_pump(self, stream_id: str, handle: Any, stream: Any) -> None:
        self.head.setdefault(stream_id, int(getattr(handle, "head_seq", 0) or 0))
        self._streams[stream_id] = stream
        self._spawn(self._pump(stream_id, stream))
        self.notify()

    async def _pump(self, stream_id: str, stream: Any) -> None:
        """Read ``stream`` until it ends; resume once per force-detach (section 9.2)."""
        while True:
            try:
                async for env in stream:
                    self._ingest(stream_id, env)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("reading %s failed", stream_id, exc_info=True)
                self.failed.add(stream_id)
                break
            reason = getattr(stream, "end_reason", None)
            resume = getattr(stream, "resume_seq", None)
            if reason == "lagged" and resume is not None:
                try:
                    _, stream = await self._client.attach(
                        stream_id, since=int(resume), deltas=False
                    )
                except Exception:
                    self.failed.add(stream_id)
                    break
                self._streams[stream_id] = stream
                continue
            if reason == REASON_DISCONNECTED:
                self.dead = True
            break
        self.notify()

    def _ingest(self, stream_id: str, env: Envelope) -> None:
        bucket = self.envs[stream_id]
        if bucket and env.seq <= bucket[-1].seq:
            return  # a resumed stream may repeat the tail
        bucket.append(env)
        event = env.event
        if isinstance(event, RunStarted):
            try:
                self.graphs[stream_id] = self._library.resolve(event.definition_ref)
            except GraphError:
                self.graphs[stream_id] = None
                logger.warning("run %s: graph %s is not in the library", stream_id,
                               event.definition_ref)
        elif isinstance(event, NodeEntered) and event.session_id:
            if event.session_id.startswith("run-"):
                self._want_stream(event.session_id, is_run=True)
            else:
                graph = self.graphs.get(stream_id)
                node = graph.node(event.node_id) if graph is not None else None
                if node is not None and speakers.is_speaker(node):
                    self._want_stream(event.session_id, is_run=False)
        self.notify()

    # -- reading the mirror --

    def last_seq(self, stream_id: str) -> int:
        bucket = self.envs.get(stream_id)
        return bucket[-1].seq if bucket else 0

    def synced(self) -> bool:
        """Every attached stream has been read up to where it was when attached."""
        for stream_id in self._want:
            if stream_id in self.failed:
                continue
            head = self.head.get(stream_id)
            if head is None or self.last_seq(stream_id) < head:
                return False
        return True

    async def wait_synced(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self.changed.clear()
            if self.synced():
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except asyncio.TimeoutError:
                return self.synced()

    async def settle(self) -> None:
        """Let reader tasks drain what the connection already delivered (live ordering)."""
        for _ in range(3):
            await asyncio.sleep(0)

    def folds(self) -> list[RunFold]:
        return [fold_run(self.envs[run_id]) for run_id in self.runs]

    def fold_of(self, stream_id: str) -> RunFold:
        return fold_run(self.envs.get(stream_id, ()))

    def turns(self) -> dict[str, quiescence.TurnState]:
        return {
            sid: quiescence.turn_state(envs)
            for sid, envs in self.envs.items()
            if sid not in self.runs
        }

    def marks(self) -> dict[str, int]:
        return {sid: self.last_seq(sid) for sid in self.envs}

    def views(
        self,
    ) -> tuple[list[Envelope], dict[str, list[Envelope]], dict[str, list[Envelope]]]:
        """``(root envelopes, child_runs, session_streams)`` as the pure modules take them."""
        child_runs = {rid: self.envs[rid] for rid in self.runs[1:]}
        sessions = {sid: envs for sid, envs in self.envs.items() if sid not in self.runs}
        return self.envs[self.root], child_runs, sessions

    def graph_node(self, stream_id: str) -> Node | None:
        """The graph node the run on ``stream_id`` currently has open (a ``human`` node)."""
        graph = self.graphs.get(stream_id)
        fold = self.fold_of(stream_id)
        if graph is None or fold.open_node is None:
            return None
        return graph.node(fold.open_node.node_id)


# ── one conversation ──────────────────────────────────────────────────────────


@dataclass
class _Conversation:
    run_id: str
    mirror: _Mirror
    host: GraphHost
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    drive: asyncio.Task[Any] | None = None
    #: The ask being re-asked (keeps ``reasks`` between messages).
    pending: PendingAsk | None = None
    #: stream id -> last seq whose words have been handed to the person.
    delivered: dict[str, int] = field(default_factory=dict)
    failure: BaseException | None = None
    closed: bool = False

    def floor_session(self) -> str | None:
        """The session whose converse node holds the floor, over a finished turn."""
        turns = self.mirror.turns()
        for fold in self.mirror.folds():
            if fold.floor is None or fold.ended is not None:
                continue
            turn = turns.get(fold.floor[1])
            if turn is not None and turn.over:
                return fold.floor[1]
        return None

    def current_pending(self) -> PendingAsk | None:
        for run_id in self.mirror.runs:
            fold = self.mirror.fold_of(run_id)
            if fold.pending_request is None:
                continue
            request = fold.pending_request
            if self.pending is not None and self.pending.req_id == request.req_id:
                return self.pending
            return asks.render(request, run_id, lambda sid, _req: self.mirror.graph_node(sid))
        return None

    async def close(self) -> None:
        self.closed = True
        if self.drive is not None and not self.drive.done():
            self.drive.cancel()
            await asyncio.gather(self.drive, return_exceptions=True)
        await self.mirror.close()


# ── the bridge ────────────────────────────────────────────────────────────────


class AlfredOmni:
    """Alfred's conversation API over omni-kernel (plan section 7.2)."""

    def __init__(
        self,
        repo_root: Path,
        settings: Settings,
        *,
        decide: Decide = decisions.choose,
        client_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._settings = settings
        self._decide = decide
        self._client_factory = client_factory
        self._client: Any = None
        self._library: GraphLibrary | None = None
        self._graphs: dict[str, Graph] = {}
        self._front: Graph | None = None
        self._policy: GraphPolicy | None = None
        self._convs: dict[str, _Conversation] = {}
        self._hidden: frozenset[str] = frozenset()
        #: Live runs this bridge started -> the name of the graph it started them with
        #: (``bridge_runs.json``, A5b). Restart only ever touches these.
        self._started: dict[str, str] = {}
        self._started_lock = asyncio.Lock()
        self._ended_folds: dict[str, RunFold] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Write the local MCP config, load the graphs, connect, re-adopt live runs (J18)."""
        if self._client is not None:
            return
        library_mod.write_local_mcp_config(self._repo_root)
        self._library, self._graphs = library_mod.load_library(self._repo_root, self._settings)
        front = self._graphs.get(self._settings.omni_front_graph)
        if front is None:
            raise library_mod.LibraryError(
                f"front graph {self._settings.omni_front_graph!r} not found in "
                f"{self._repo_root / library_mod.GRAPHS_SUBDIR}"
            )
        self._front = front
        self._policy = GraphPolicy(self._library)
        self._hidden = await asyncio.to_thread(self._read_hidden)
        self._started = await asyncio.to_thread(self._read_started)
        self._client = await self._connect()
        adopted = await self._readopt()
        logger.info("omni bridge started: %d graph(s), %d live conversation(s) re-adopted",
                    len(self._graphs), adopted)

    async def close(self) -> None:
        convs = list(self._convs.values())
        self._convs.clear()
        for conv in convs:
            with contextlib.suppress(Exception):
                await conv.close()
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    async def healthy(self) -> bool:
        """Connected, and the daemon answers a cheap listing."""
        if self._client is None:
            return False
        try:
            await asyncio.wait_for(self._client.list_runs(module=GRAPH_MODULE, limit=1), 5.0)
        except Exception:
            return False
        return True

    async def _connect(self) -> Any:
        try:
            if self._client_factory is not None:
                return await self._client_factory()
            await self._prestart_daemon()
            try:
                from omni.cli.connect import open_client
            except ImportError as exc:
                # Alfred's venv has prompt_toolkit now, so this is only a fallback for a venv
                # without the CLI extra: no find-or-spawn then, but a running daemon can be joined.
                logger.info("omni.cli is unavailable (%s); joining a running daemon", exc.name)
                return await _join_running_daemon()
            return await open_client(spawn=True)
        except (UnavailableError, OSError) as exc:
            raise BridgeUnavailable(f"cannot reach the omni daemon: {exc}") from exc

    async def _prestart_daemon(self) -> None:
        """J16's secondary path: put the Jev key in the env of a daemon *this* call spawns.

        ``open_client`` has no hook for the child's environment, but ``ensure_daemon`` takes
        the spawn function, so when no daemon is running and ``TYPESAFE_API_KEY`` is unset the
        key file is read (``KeyFileCredential``) and handed to that one spawn only. The key is
        never logged or stored. Any failure here is non-fatal: ``open_client`` then does its
        own find-or-spawn and decide nodes fall back as J16 describes.
        """
        if os.environ.get("TYPESAFE_API_KEY"):
            return
        try:
            from omni.cli.connect import ensure_daemon, screen_protocol, spawn_detached
            from omni.driver.provider import KeyFileCredential

            key = KeyFileCredential(self._settings.jev_key_file).resolve()
            if not key:
                return

            def spawn(argv: Sequence[str], env: dict[str, str] | None = None) -> Any:
                child_env = dict(os.environ if env is None else env)
                child_env["TYPESAFE_API_KEY"] = key
                return spawn_detached(argv, env=child_env)

            record = read_discovery()
            if record is not None:
                await asyncio.to_thread(screen_protocol, record)
            await ensure_daemon(spawn=spawn)
        except Exception as exc:
            logger.warning("could not pre-start the omni daemon with the Jev key: %s",
                           type(exc).__name__)

    # ── re-adoption (J18) ────────────────────────────────────────────────────

    async def _readopt(self) -> int:
        """Re-adopt the live runs this bridge started; touch no other run (J18, A5b).

        ``bridge_runs.json`` is the whole scope: another client's run in the same daemon, or a
        subgraph's child run, is not in it and is left alone. A recorded run that is no longer
        live is pruned from the record. For the rest, the graph the run started with may be
        gone (its ref is a content hash, and the hash covers the injected house constraints),
        so the graph of the **same name** drives it if the run's history still fits that graph
        (``_heal``); otherwise the run is cancelled with reason ``graph changed``.
        """
        live = {
            info.run_id
            for info in await self._kcall(self._client.list_runs, module=GRAPH_MODULE, limit=1000)
        }
        kept: dict[str, str] = {}
        adopted: list[str] = []
        for run_id, name in self._started.items():
            if run_id not in live or run_id in self._hidden:
                continue  # finished, forgotten or gone: nothing to adopt, prune the record
            graph = self._graphs.get(name)
            try:
                fits = graph is not None and await self._heal(run_id, graph)
            except (NotFoundError, ConversationNotFound):
                continue  # vanished while we looked
            if not fits:
                logger.warning("run %s: graph %r changed; cancelling", run_id, name)
                with contextlib.suppress(Exception):
                    await self._client.cancel_run(run_id, "graph changed")
                continue
            kept[run_id] = name
            adopted.append(run_id)
        if kept != self._started:
            self._started = kept
            await asyncio.to_thread(self._write_started, dict(kept))
        results = await asyncio.gather(*(self._adopt(r) for r in adopted), return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, BaseException))

    async def _heal(self, run_id: str, graph: Graph) -> bool:
        """Whether ``run_id`` (and its subgraph runs) can be driven with the library as it is.

        A run whose ``definition_ref`` still resolves needs nothing. One whose ref is gone is
        pointed at ``graph`` (the same-name graph, ``library_mod.alias_ref``) provided every
        node it has entered exists in ``graph``, a ``graph`` node still is one, and its
        subgraph runs can be healed the same way against the subgraph that node now names.
        Nothing is aliased unless the whole tree fits.
        """
        assert self._library is not None
        envs = await self._peek_envs(run_id)
        started = next((e.event for e in envs if isinstance(e.event, RunStarted)), None)
        if started is None:
            return False
        stale = started.definition_ref not in self._library
        current = graph if stale else self._library.resolve(started.definition_ref)
        for env in envs:
            event = env.event
            if not isinstance(event, NodeEntered):
                continue
            node = current.node(event.node_id)
            is_child = event.session_id.startswith("run-")
            if node is None:
                return False
            if stale and (node.kind == "graph") != is_child:
                return False
            if node.kind != "graph" or not is_child:
                continue
            try:
                child = self._library.resolve(node.graph or "")
            except GraphError:
                return False
            if not await self._heal(event.session_id, child):
                return False
        if stale:
            library_mod.alias_ref(self._library, started.definition_ref, graph)
        return True

    async def _adopt(self, run_id: str) -> None:
        conv = await self._open(run_id)
        await conv.mirror.wait_synced(SYNC_WAIT)
        # After a restart nobody knows what was said; assume the history was seen. A pending
        # ask is re-surfaced by the next say() (its question is re-rendered from the journal).
        conv.delivered = conv.mirror.marks()
        logger.info("re-adopted conversation %s", run_id)

    # ── the conversation API ─────────────────────────────────────────────────

    async def say(self, text: str, conversation_id: str | None) -> ConversationReply:
        """The ``/execute`` path; routing per section 7.8."""
        self._require()
        conv = None
        if conversation_id and conversation_id not in self._hidden:
            conv = self._convs.get(conversation_id)
        if conv is None:  # None, unknown, hidden or ended -> a fresh conversation (J11)
            return await self._begin(text)
        if conv.lock.locked():
            return self._reply_of(conv, STILL_ON_IT, "working")
        async with conv.lock:
            if conv.closed:
                return await self._begin(text)
            return await self._route(conv, text)

    async def continue_from(self, conversation_id: str, text: str) -> ConversationReply:
        """Load an earlier conversation (J11): a new run seeded with its transcript."""
        self._require()
        entries = await self._entries(conversation_id)
        earlier = transcript_mod.earlier_text(entries, self._settings.omni_earlier_chars)
        return await self._begin(text, earlier=earlier, continues=conversation_id)

    async def answer_ask(
        self, conversation_id: str, req_id: str, decision: str
    ) -> ConversationReply:
        """Answer the conversation's pending ask directly (J13, for UIs with buttons).

        ``ConversationNotFound`` for an id that is not a live conversation; ``ValueError`` when
        ``req_id`` is not the pending ask, or ``decision`` is not one of its options.
        """
        self._require()
        conv = self._convs.get(conversation_id)
        if conv is None or conversation_id in self._hidden:
            raise ConversationNotFound(conversation_id)
        async with conv.lock:
            if conv.closed:
                raise ConversationNotFound(conversation_id)
            await conv.mirror.wait_synced(SYNC_WAIT)
            pending = conv.current_pending()
            if pending is None or pending.req_id != req_id:
                raise ValueError(f"{req_id!r} is not the pending ask of {conversation_id}")
            if pending.options and decision not in pending.options:
                raise ValueError(
                    f"decision {decision!r} is not one of {', '.join(pending.options)}"
                )
            if not pending.options and not decision.strip():
                raise ValueError("an empty decision")
            return await self._resolve(conv, pending, asks.step(pending, decision, ""))

    async def list_conversations(
        self, *, include_hidden: bool = False
    ) -> list[ConversationSummary]:
        """Front-door conversations, newest first (live ones from their mirror)."""
        self._require()
        runs = await self._kcall(
            self._client.list_runs, module=GRAPH_MODULE, include_ended=True, limit=1000
        )
        runs = sorted(runs, key=lambda info: info.started_ts)[-LIST_SCAN:]
        gate = asyncio.Semaphore(PEEK_PARALLEL)

        async def one(run_id: str) -> ConversationSummary | None:
            if not include_hidden and run_id in self._hidden:
                return None
            async with gate:
                try:
                    return await self._summary(run_id)
                except (ConversationNotFound, NotFoundError):
                    return None

        rows = await asyncio.gather(*(one(info.run_id) for info in runs))
        found = [row for row in rows if row is not None]
        found.sort(key=lambda row: row.last_active, reverse=True)
        return found[:LIST_MAX]

    async def transcript(self, conversation_id: str) -> Transcript:
        """The conversation as the person saw it, rebuilt from the journal (section 7.7)."""
        self._require()
        async with self._mirror_of(conversation_id) as (mirror, conv):
            run_envs, child_runs, sessions = mirror.views()
            entries = transcript_mod.build(run_envs, child_runs, sessions, self._library)
            summary = self._summary_of(mirror, conv)
        return Transcript(conversation=summary, entries=entries)

    async def forget(self, conversation_id: str) -> bool:
        """Cancel the conversation if it is live and hide it (section 7.9)."""
        self._require()
        known = conversation_id in self._hidden
        conv = self._convs.get(conversation_id)
        if conv is not None:
            known = True
            with contextlib.suppress(Exception):
                await self._client.cancel_run(conversation_id, "forgotten")
            self._convs.pop(conversation_id, None)
            await self._remember(conversation_id, None)
            await conv.close()
        elif not known:
            try:
                await self._summary(conversation_id)
            except (ConversationNotFound, NotFoundError):
                return False
        self._hidden = with_hidden(self._hidden, conversation_id)
        await asyncio.to_thread(self._write_hidden, self._hidden)
        return True

    # ── routing (section 7.8) ────────────────────────────────────────────────

    async def _route(self, conv: _Conversation, text: str) -> ConversationReply:
        mirror = conv.mirror
        await mirror.wait_synced(SYNC_WAIT)
        folds = mirror.folds()
        if folds[0].ended is not None:  # ended while nobody was waiting
            leftovers = self._pieces(conv)
            await self._retire(conv)
            await conv.close()
            return await self._begin(text, preface=leftovers)
        state = quiescence.state_of(folds, mirror.turns())
        if state == "awaiting_approval":
            pending = conv.current_pending()
            if pending is not None:
                return await self._answer(conv, pending, text)
        floor = conv.floor_session() if state == "awaiting_user" else None
        if floor is not None:
            submitted = mirror.marks()
            try:
                await self._kcall(self._client.prompt, floor, text)
            except ConflictError:
                return self._reply_of(conv, STILL_ON_IT, "working")
            return await self._collect(conv, submitted)
        leftovers = self._pieces(conv)
        if leftovers:
            conv.delivered = mirror.marks()
            return self._reply_of(conv, "\n\n".join(leftovers), "working")
        return self._reply_of(conv, STILL_ON_IT, "working")

    async def _answer(
        self, conv: _Conversation, pending: PendingAsk, text: str
    ) -> ConversationReply:
        """A message arrived while an ask was pending (J13/J14): it is the answer."""
        answer = await asks.interpret(pending, text, self._decide)
        return await self._resolve(conv, pending, asks.step(pending, answer, text))

    async def _resolve(
        self, conv: _Conversation, pending: PendingAsk, step: asks.AskStep
    ) -> ConversationReply:
        if step.option is None:  # not understood: ask again, keep the ask parked
            conv.pending = step.pending
            return ConversationReply(
                conversation_id=conv.run_id,
                answer=step.say,
                state="awaiting_approval",
                pending=step.pending,
            )
        submitted = conv.mirror.marks()
        answered: tuple[str, str] | None = (pending.stream_id, pending.req_id)
        try:
            await self._kcall(
                self._client.respond,
                pending.stream_id,
                pending.req_id,
                step.word,
                data=dict(step.data) if step.data else None,
            )
        except (ConflictError, NotFoundError) as exc:
            # Already answered (e.g. the ask timed out): carry on and report what happened.
            logger.warning("ask %s could not be answered: %s", pending.req_id, type(exc).__name__)
            answered = None
        conv.pending = None
        return await self._collect(
            conv, submitted, answered=answered, preface=[step.say] if step.say else []
        )

    # ── starting runs ────────────────────────────────────────────────────────

    async def _begin(
        self,
        text: str,
        *,
        earlier: str = "",
        continues: str = "",
        preface: Sequence[str] = (),
        allow_handoff: bool = True,
    ) -> ConversationReply:
        assert self._front is not None
        info = await self._kcall(
            start_graph_run,
            self._client,
            self._front,
            {"message": text, "earlier": earlier, "continues": continues},
        )
        await self._remember(info.run_id, self._front.name)
        conv = await self._open(info.run_id)
        async with conv.lock:
            reply = await self._collect(
                conv, {}, preface=preface, allow_handoff=allow_handoff
            )
        return reply.model_copy(
            update={"new_conversation": True, "continued_from": continues or None}
        )

    async def _open(self, run_id: str) -> _Conversation:
        assert self._library is not None and self._policy is not None
        mirror = _Mirror(self._client, self._library)
        try:
            await mirror.open(run_id)
        except NotFoundError as exc:
            raise ConversationNotFound(run_id) from exc
        except UnavailableError as exc:
            raise BridgeUnavailable(str(exc)) from exc
        host = GraphHost(self._client, self._policy, on_request=self._on_request(mirror))
        conv = _Conversation(run_id, mirror, host)
        conv.drive = asyncio.ensure_future(self._drive(conv))
        self._convs[run_id] = conv
        return conv

    @staticmethod
    def _on_request(mirror: _Mirror) -> Callable[[str, Request], None]:
        def hook(_stream_id: str, _request: Request) -> None:
            mirror.notify()  # the mirror's own reader folds the Request; this just wakes waiters

        return hook

    async def _drive(self, conv: _Conversation) -> None:
        try:
            await conv.host.drive(conv.run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("graph host for %s failed: %s", conv.run_id, type(exc).__name__,
                         exc_info=True)
            conv.failure = exc
        finally:
            conv.mirror.notify()

    # ── waiting and replying ─────────────────────────────────────────────────

    async def _wait(
        self,
        conv: _Conversation,
        submitted: Mapping[str, int],
        answered: tuple[str, str] | None,
    ) -> ConversationState:
        """Read until quiescent (section 7.4); ``working`` on timeout (J19)."""
        mirror = conv.mirror
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.omni_turn_timeout
        while True:
            mirror.changed.clear()
            if mirror.dead:
                raise BridgeUnavailable("the connection to the omni daemon was lost")
            state: ConversationState | None = None
            if answered is None or answered[1] in mirror.fold_of(answered[0]).answers:
                state = quiescence.state_of(mirror.folds(), mirror.turns(), submitted)
            if state is not None and mirror.synced():
                return state
            if conv.failure is not None and conv.drive is not None and conv.drive.done():
                return "ended"
            remaining = deadline - loop.time()
            if remaining <= 0:
                return "working"
            try:
                await asyncio.wait_for(mirror.changed.wait(), remaining)
            except asyncio.TimeoutError:
                return "working"

    async def _collect(
        self,
        conv: _Conversation,
        submitted: Mapping[str, int],
        *,
        answered: tuple[str, str] | None = None,
        preface: Sequence[str] = (),
        allow_handoff: bool = True,
    ) -> ConversationReply:
        state = await self._wait(conv, submitted, answered)
        mirror = conv.mirror
        await mirror.settle()
        pieces = [*preface]
        for piece in self._pieces(conv):
            if piece not in pieces:  # a denied call is spoken by the tool node and by step.say
                pieces.append(piece)
        pending = conv.current_pending() if state == "awaiting_approval" else None
        answer = speakers.compose_reply(pieces, state, pending)
        conv.delivered = mirror.marks()
        reply = ConversationReply(
            conversation_id=conv.run_id, answer=answer, state=state, pending=pending
        )
        if state == "ended":
            return await self._ended(conv, reply, allow_handoff)
        return reply

    def _pieces(self, conv: _Conversation) -> list[str]:
        run_envs, child_runs, sessions = conv.mirror.views()
        return speakers.spoken(
            run_envs, child_runs, sessions, self._library, since=conv.delivered
        )

    def _reply_of(
        self, conv: _Conversation, answer: str, state: ConversationState
    ) -> ConversationReply:
        return ConversationReply(conversation_id=conv.run_id, answer=answer, state=state)

    async def _ended(
        self, conv: _Conversation, reply: ConversationReply, allow_handoff: bool
    ) -> ConversationReply:
        """The run is over: tidy up, and follow a ``new-request`` handoff (J10)."""
        mirror = conv.mirror
        fold = mirror.folds()[0]
        run_envs, child_runs, sessions = mirror.views()
        if not reply.answer.strip():
            outcome = fold.ended.outcome if fold.ended is not None else "failed"
            if conv.failure is not None or outcome not in ("completed", "cancelled"):
                reply = reply.model_copy(update={"answer": WENT_WRONG})
        handoff = (
            transcript_mod.handoff_text(fold, sessions)
            if allow_handoff and fold.ended is not None
            else None
        )
        earlier = ""
        if handoff:
            entries = transcript_mod.build(run_envs, child_runs, sessions, self._library)
            earlier = transcript_mod.earlier_text(entries, self._settings.omni_earlier_chars)
        if conv.failure is not None and fold.ended is None:
            with contextlib.suppress(Exception):
                await self._client.cancel_run(conv.run_id, "bridge error")
        await self._retire(conv)
        await conv.close()
        if not handoff:
            return reply
        nxt = await self._begin(handoff, earlier=earlier, continues=conv.run_id,
                                allow_handoff=False)
        joined = "\n\n".join(t for t in (reply.answer.strip(), nxt.answer.strip()) if t)
        return nxt.model_copy(update={"answer": joined, "continued_from": conv.run_id})

    async def _retire(self, conv: _Conversation) -> None:
        if self._convs.get(conv.run_id) is conv:
            del self._convs[conv.run_id]
        await self._remember(conv.run_id, None)

    # ── reading old conversations ────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def _mirror_of(self, conversation_id: str):
        """``(mirror, live_conversation | None)`` for an id: the live one, else a replay."""
        if conversation_id in self._hidden or not conversation_id.startswith("run-"):
            raise ConversationNotFound(conversation_id)
        conv = self._convs.get(conversation_id)
        if conv is not None:
            await conv.mirror.wait_synced(SYNC_WAIT)
            yield conv.mirror, conv
            return
        mirror = _Mirror(self._client, self._library)
        try:
            try:
                await mirror.open(conversation_id)
            except NotFoundError as exc:
                raise ConversationNotFound(conversation_id) from exc
            except UnavailableError as exc:
                raise BridgeUnavailable(str(exc)) from exc
            await mirror.wait_synced(SYNC_WAIT)
            if not self._is_front_run(mirror.folds()[0]):
                raise ConversationNotFound(conversation_id)
            yield mirror, None
        finally:
            await mirror.close()

    @staticmethod
    def _is_front_run(fold: RunFold) -> bool:
        """Front-door runs carry ``message`` and ``continues`` (J12); subgraph runs do not."""
        return fold.started and "message" in fold.inputs and "continues" in fold.inputs

    async def _entries(self, conversation_id: str) -> list[Any]:
        async with self._mirror_of(conversation_id) as (mirror, _conv):
            run_envs, child_runs, sessions = mirror.views()
            return transcript_mod.build(run_envs, child_runs, sessions, self._library)

    def _summary_of(self, mirror: _Mirror, conv: _Conversation | None) -> ConversationSummary:
        folds = mirror.folds()
        summary = transcript_mod.summarize(folds[0], mirror.turns())
        if conv is not None:
            state = quiescence.state_of(folds, mirror.turns()) or "working"
            summary = summary.model_copy(update={"state": state})
        return summary

    async def _summary(self, run_id: str) -> ConversationSummary:
        """The listing row for ``run_id``; reads only its own run stream unless it is live."""
        conv = self._convs.get(run_id)
        if conv is not None:
            return self._summary_of(conv.mirror, conv)
        fold = self._ended_folds.get(run_id)
        if fold is None:
            fold = await self._peek(run_id)
            if fold.ended is not None:
                if len(self._ended_folds) >= ENDED_CACHE_MAX:
                    self._ended_folds.pop(next(iter(self._ended_folds)))
                self._ended_folds[run_id] = fold
        if not self._is_front_run(fold):
            raise ConversationNotFound(run_id)
        return transcript_mod.summarize(fold, {})

    async def _peek(self, run_id: str) -> RunFold:
        """Fold one run stream (no child runs, no sessions)."""
        return fold_run(await self._peek_envs(run_id))

    async def _peek_envs(self, run_id: str) -> list[Envelope]:
        """The envelopes of one run stream, read to its head and detached again."""
        if not run_id.startswith("run-"):
            raise ConversationNotFound(run_id)
        try:
            handle, stream = await self._client.attach(run_id, since=0, deltas=False)
        except NotFoundError as exc:
            raise ConversationNotFound(run_id) from exc
        except UnavailableError as exc:
            raise BridgeUnavailable(str(exc)) from exc
        head = int(getattr(handle, "head_seq", 0) or 0)
        envs: list[Envelope] = []
        try:
            if head > 0:
                async for env in stream:
                    envs.append(env)
                    if env.seq >= head:
                        break
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
        return envs

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _require(self) -> None:
        if self._client is None or self._library is None:
            raise BridgeUnavailable("the omni bridge is not started")

    async def _kcall(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """Await a kernel call; a lost connection becomes ``BridgeUnavailable``."""
        try:
            return await fn(*args, **kwargs)
        except UnavailableError as exc:
            raise BridgeUnavailable(str(exc)) from exc
        except (ConnectionError, BrokenPipeError) as exc:
            raise BridgeUnavailable(f"connection to the omni daemon failed: {exc}") from exc

    def _read_hidden(self) -> frozenset[str]:
        path = hidden_path(self._settings.data_dir)
        try:
            return parse_hidden(path.read_text(encoding="utf-8"))
        except OSError:
            return frozenset()

    def _write_hidden(self, ids: frozenset[str]) -> None:
        self._write_file(hidden_path(self._settings.data_dir), render_hidden(ids))

    def _read_started(self) -> dict[str, str]:
        path = runs_path(self._settings.data_dir)
        try:
            return parse_runs(path.read_text(encoding="utf-8"))
        except OSError:
            return {}

    def _write_started(self, runs: Mapping[str, str]) -> None:
        self._write_file(runs_path(self._settings.data_dir), render_runs(runs))

    async def _remember(self, run_id: str, graph_name: str | None) -> None:
        """Record a run this bridge started (``graph_name``), or drop it (``None``)."""
        async with self._started_lock:
            if graph_name is None:
                if self._started.pop(run_id, None) is None:
                    return
            else:
                self._started[run_id] = graph_name
            await asyncio.to_thread(self._write_started, dict(self._started))

    @staticmethod
    def _write_file(path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            logger.warning("could not write %s", path, exc_info=True)
