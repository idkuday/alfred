"""Live end-to-end smoke for the omni bridge (omni-jev plan, task A7).

Drives plan section 9.1 to 9.4 through the real thing: ``AlfredOmni`` -> a real omni daemon ->
the real ``front-door`` graph -> Alfred's Home Assistant MCP server -> the **mock** Home Assistant
(``ai_server/mcp/mock_ha.py``; no real house is touched). Real Jev decides when you pass
``--jev``; otherwise decide nodes degrade to the LLM path and the script still runs.

Run it from the repo root (the coordinator's live command is in ``docs/omni.md``)::

    python -m ai_server.omni_bridge.smoke --dry-run            # no secrets, no LLM calls
    python -m ai_server.omni_bridge.smoke                      # LLM path for decide nodes
    python -m ai_server.omni_bridge.smoke --jev --jev-key-file <path to jev.env>

Why it is built the way it is:

* **Isolated daemon.** ``OMNI_HOME`` (discovery directory) and the journal location
  (``LOCALAPPDATA`` on Windows, ``XDG_STATE_HOME`` elsewhere) are redirected to a temp directory
  *in this process's environment before the bridge connects*, so the daemon the bridge spawns is
  a private one with an empty journal: the user's own daemon and ``~/.omni`` journal are never
  touched, and the bridge's J18 re-adoption cannot cancel their runs. Provider config is still
  read from ``~/.omni/providers`` (omni resolves it from the home directory, not ``OMNI_HOME``).
  The daemon is stopped, and the temp directory removed, on every exit path. ``--shared-daemon``
  opts out (the daemon already running is used and left running).
* **The Jev key never leaves the key file.** Without ``--jev`` the key file setting points at a
  file that does not exist and ``TYPESAFE_API_KEY`` is removed from the environment, so nothing
  reaches Jev. With ``--jev`` only the *path* is configured: the bridge's J16 path
  (``AlfredOmni._prestart_daemon``) reads the key through ``KeyFileCredential`` and puts it in
  the environment of the one daemon it spawns. This script never reads, prints, logs or stores
  the key; it only asks whether the file yields a key (``available()``).
* **The mock's URL reaches the MCP server through ``mcp.local.json``.** The daemon spawns the MCP
  servers, so ``HA_URL``/``HA_TOKEN``/``JEV_KEY_FILE`` go into the ``env`` of the
  ``home_assistant`` entry of the generated, git-ignored ``.omni/mcp.local.json`` (the kernel merges
  ``env`` over the inherited environment). The file is regenerated clean on exit.
* **Path taken** is read from the journal by a second, read-only client: the node ids, the
  ``Decided`` choices (with provider/model/confidence/latency) and the tool calls of the
  conversation's run and its child runs.
* **A hard overall timeout** (``--timeout``) bounds the whole script; every bridge call is bounded
  too, so a stuck daemon is reported rather than waited on.

Exit status: 0 all scenarios passed (or the dry run's checks did), 1 a scenario failed,
2 a precondition is missing (nothing was started), 3 the overall timeout fired.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT, settings

logger = logging.getLogger(__name__)

SCENARIOS = ("9.1", "9.2", "9.3", "9.4")
#: A scenario that needs an earlier one's conversation: 9.4 continues the conversation 9.1 ended
#: (a deterministic, Jev-routed one). 9.2 and 9.3 each open their own troubleshooting conversation,
#: so a model that misbehaves in one cannot hide the other.
PREREQUISITES = {"9.1": (), "9.2": (), "9.3": (), "9.4": ("9.1",), "9.3x": ()}
#: Optional extras, run only with ``--extras`` or named in ``--only``: 9.3x is 9.3's handoff out of a
#: *troubleshooting intake*, which depends on the model choosing to ask a question.
EXTRA_SCENARIOS = ("9.3x",)
#: 9.3 opens with a request ``front`` routes to ``chat``, a converse node that always holds the floor
#: and exits ``new-request`` (J10) when the person asks for something to be done.
#: Worded to keep the conversation open: "how are you?" alone was answered and then ended by the model
#: (`chat=done`) on a live run, which is legitimate but leaves nothing to hand off from.
CHAT_OPENER = ("let's have a proper chat, I have time. Ask me how my day was, and wait for my "
               "answer before you say anything else")
TOPIC_CHANGE = "actually, turn on the coffee maker"

#: What Alfred says when a run died silently (``client.WENT_WRONG``); matched loosely, lower case.
WENT_WRONG_MARK = "something went wrong on my side"

EXIT_OK, EXIT_FAIL, EXIT_PRECONDITION, EXIT_TIMEOUT = 0, 1, 2, 3

#: Seconds allowed for the daemon to start and the bridge to connect.
START_TIMEOUT = 60.0
#: Slack added to ``omni_turn_timeout`` for one bridge call (the bridge enforces its own).
CALL_SLACK = 30.0
#: The opener for 9.2 and 9.3. It names no device and no symptom, so the intake agent has nothing to
#: look up and has to ask ("one of my sensors keeps going offline" was tried first: the model sometimes
#: guessed a sensor by id instead of asking, and the Home Assistant list tool does not list sensors).
AMBIGUOUS_OPENER = "something in the house is not working and I don't know what"
#: The user's answer to the intake's question in 9.2: names the device and the symptom.
INTAKE_ANSWER = "it's the garage motion sensor, it keeps going offline"

#: Extra sensors seeded into the mock for the troubleshooting scenarios (state is per server
#: instance, so nothing leaks into the shared fixture or other tests).
EXTRA_SENSORS = {
    "binary_sensor.garage_motion": ("Garage Motion", "off"),
    "binary_sensor.basement_motion": ("Basement Motion", "off"),
    "binary_sensor.front_door_contact": ("Front Door Contact", "off"),
}

#: Node ids that must NOT appear in a device-command run (9.1: "no chat model ran").
CHAT_NODES = frozenset({"device-help", "chat", "clarify", "troubleshoot", "routine"})


# ── small helpers ────────────────────────────────────────────────────────────


def say_out(text: str = "") -> None:
    print(text, flush=True)


def clip(text: str, limit: int = 400) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def node_llm_choice(repo_root: Path) -> tuple[str, str, str]:
    """``(provider, model, source)`` the agent nodes' chat model comes from.

    Settings win (``omni_node_provider`` / ``omni_node_model``, which the bridge injects into every
    agent/tool node), then ``.omni/alfred/agent.yml``.
    """
    provider = model = ""
    source = ".omni/alfred/agent.yml"
    try:
        import yaml

        data = yaml.safe_load((repo_root / ".omni" / "alfred" / "agent.yml").read_text("utf-8"))
        if isinstance(data, dict):
            provider = str(data.get("provider") or "")
            model = str(data.get("model") or "")
    except Exception as exc:  # unreadable manifest: say so, do not guess
        logger.warning("cannot read agent.yml: %s", type(exc).__name__)
    if settings.omni_node_provider:
        provider, source = settings.omni_node_provider, "OMNI_NODE_PROVIDER override"
        model = settings.omni_node_model or "(provider default)"
        if settings.omni_node_model:
            source = "OMNI_NODE_PROVIDER / OMNI_NODE_MODEL override"
    elif settings.omni_node_model:
        model, source = settings.omni_node_model, "agent.yml provider + OMNI_NODE_MODEL override"
    return provider or "(unknown)", model or "(unknown)", source


def provider_key_available(provider: str) -> bool | None:
    """Whether ``provider`` has a credential (never reads it out). ``None`` = needs none/unknown.

    Same chain omni's providers use: ``<NAME>_API_KEY`` in the environment, else
    ``~/.omni/providers/<name>/config.yml`` (``api_key`` or ``api_key_file``).
    """
    if provider in ("", "(unknown)", "ollama"):
        return None
    from omni.driver.provider.credentials import EnvCredential, FileCredential, FirstOf

    chain = FirstOf(EnvCredential(f"{provider.upper()}_API_KEY"), FileCredential(provider))
    return chain.available()


# ── the journal observer ─────────────────────────────────────────────────────


@dataclass
class Segment:
    node: str
    run: str
    notes: list[str] = field(default_factory=list)
    outcome: str | None = None

    def render(self, root_run: str) -> str:
        prefix = "" if self.run == root_run else f"{self.run}:"
        body = f"{prefix}{self.node}"
        if self.notes:
            body += "[" + "; ".join(self.notes) + "]"
        body += f"={self.outcome}" if self.outcome is not None else "…"
        return body


class Observer:
    """Reads a conversation's journal (the run and its child runs) through a second client.

    ``steps(run_id)`` returns the segments that appeared since its previous call, so each reply
    shows only the path it added.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._cursor: dict[str, int] = {}
        self._children: dict[str, list[str]] = {}

    async def _envelopes(self, stream_id: str) -> list[Any]:
        handle, stream = await self._client.attach(stream_id, since=0, deltas=False)
        head = int(getattr(handle, "head_seq", 0) or 0)
        envs: list[Any] = []
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

    async def steps(self, run_id: str) -> list[Segment]:
        from omni.kernel.events import (
            CallEnded,
            CallStarted,
            Decided,
            NodeEntered,
            NodeExited,
        )

        out: list[Segment] = []
        envs = await self._envelopes(run_id)
        cursor = self._cursor.get(run_id, 0)
        current: Segment | None = None
        visited: set[str] = set()
        by_call: dict[str, str] = {}
        for env in envs:
            if env.seq <= cursor:
                continue
            event = env.event
            if isinstance(event, NodeEntered):
                current = Segment(event.node_id, run_id)
                out.append(current)
                if event.session_id and event.session_id.startswith("run-"):
                    kids = self._children.setdefault(run_id, [])
                    if event.session_id not in kids:
                        kids.append(event.session_id)
                    visited.add(event.session_id)
                    out.extend(await self.steps(event.session_id))
            elif isinstance(event, Decided):
                seg = self._segment(out, event.node_id, run_id)
                seg.notes.append(
                    f"decided {event.choice} {event.confidence:.2f} "
                    f"{event.provider}/{event.model} {event.latency_ms}ms"
                )
            elif isinstance(event, CallStarted):
                seg = current or self._segment(out, "?", run_id)
                by_call[event.call_id] = event.name
                seg.notes.append(f"call {event.name}")
            elif isinstance(event, CallEnded):
                seg = current or self._segment(out, "?", run_id)
                seg.notes.append(f"{by_call.get(event.call_id, 'call')} {event.outcome}")
            elif isinstance(event, NodeExited):
                seg = self._segment(out, event.node_id, run_id, create=True)
                seg.outcome = event.outcome
        if envs:
            self._cursor[run_id] = envs[-1].seq
        # A child run entered in an earlier call still moves: read what it added since.
        for kid in self._children.get(run_id, []):
            if kid not in visited:
                out.extend(await self.steps(kid))
        return out

    @staticmethod
    def _segment(out: list[Segment], node: str, run: str, *, create: bool = True) -> Segment:
        for seg in reversed(out):
            if seg.node == node and seg.run == run and seg.outcome is None:
                return seg
        seg = Segment(node, run)
        out.append(seg)
        return seg

    async def inputs(self, run_id: str) -> dict[str, Any]:
        from omni.kernel.events import RunStarted

        for env in await self._envelopes(run_id):
            if isinstance(env.event, RunStarted):
                return dict(env.event.inputs)
        return {}


def decided_providers(segments: list[Segment]) -> set[str]:
    found: set[str] = set()
    for seg in segments:
        for note in seg.notes:
            if note.startswith("decided "):
                found.add(note.split()[3].split("/")[0])
    return found


# ── results ──────────────────────────────────────────────────────────────────


@dataclass
class Scenario:
    name: str
    title: str
    failures: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failures

    def expect(self, condition: Any, message: str) -> bool:
        if not condition:
            self.failures.append(message)
        return bool(condition)

    def finish(self) -> None:
        self.elapsed = time.monotonic() - self.started
        verdict = "PASS" if self.ok else "FAIL"
        say_out(f"[{self.name}] {verdict}  {self.title}  ({self.elapsed:.1f}s)")
        for failure in self.failures:
            say_out(f"      - {failure}")
        say_out()


# ── the harness ──────────────────────────────────────────────────────────────


class Harness:
    """Everything the smoke starts, and the one place that stops it."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tmp: Path | None = None
        self.mock_url = ""
        self.mock: Any = None
        self.bridge: Any = None
        self.observer_client: Any = None
        self.observer: Observer | None = None
        self.isolated = False
        self.jev_on = False
        self.mcp_local: Path | None = None
        self.mcp_patched = False
        self.jev_key_path = ""
        self.started_ids: list[str] = []
        self.last_path = ""

    # -- preconditions -------------------------------------------------------

    def preflight(self) -> int:
        """Print the plan and refuse early, with a clear message, when it cannot run."""
        a = self.args
        say_out("alfred omni smoke (omni-jev A7)")
        say_out(f"  repo           : {REPO_ROOT}")
        try:
            import omni
            from omni.kernel.api import PROTOCOL_VERSION

            say_out(f"  omni           : {Path(omni.__file__).parent} (protocol {PROTOCOL_VERSION})")
        except Exception as exc:
            say_out(f"  omni           : NOT IMPORTABLE ({type(exc).__name__}: {exc})")
            return EXIT_PRECONDITION

        # Jev
        from omni.driver.provider.credentials import FileCredential, KeyFileCredential

        if a.jev:
            key_path = Path(a.jev_key_file or settings.jev_key_file)
            if not KeyFileCredential(key_path).available():
                say_out(f"  jev            : NO KEY in {key_path} (missing, empty or unreadable)")
                say_out("ERROR: --jev needs a Jev key file; pass --jev-key-file <path> or omit --jev.")
                return EXIT_PRECONDITION
            self.jev_key_path = str(key_path)
            self.jev_on = True
            say_out(f"  jev            : ON (key from {key_path}; handed to the spawned daemon "
                    "in-process, never printed)")
        else:
            self.jev_key_path = ""
            note = ""
            if FileCredential("typesafe").available():
                note = " (but ~/.omni/providers/typesafe has a key, so the daemon may still use Jev)"
            say_out("  jev            : OFF (decide nodes degrade to the LLM path; pass --jev for "
                    f"real Jev){note}")

        # The chat model of the agent nodes (and of the decide fallback).
        provider, model, source = node_llm_choice(REPO_ROOT)
        has_key = provider_key_available(provider)
        key_note = {True: "key found", None: "no key needed / unknown", False: "NO KEY"}[has_key]
        say_out(f"  node LLM       : {provider}/{model}  (effective for every agent/tool node; "
                f"from {source}; {key_note})")
        say_out("  decide nodes   : "
                + ("typesafe/jev-latest (graph constraints)" if self.jev_on
                   else "typesafe/jev-latest requested by the graphs, falls back without a key"))

        wanted = self.scenarios()
        say_out(f"  scenarios      : {', '.join(wanted) if not a.dry_run else '(dry run: none)'}")
        say_out(f"  daemon         : {'shared (already running or spawned, left alone)' if a.shared_daemon else 'isolated (private OMNI_HOME + journal, stopped on exit)'}")
        say_out(f"  timeouts       : overall {a.timeout:g}s, per turn {a.turn_timeout:g}s")

        needs_llm = (not self.jev_on) or any(s != "9.1" for s in wanted)
        if has_key is False and needs_llm and not a.dry_run:
            say_out()
            say_out(f"ERROR: the node LLM provider '{provider}' has no API key, and this run needs it "
                    f"({'decide nodes fall back to it without --jev' if not self.jev_on else 'agent nodes of 9.2-9.4 run on it'}).")
            say_out(f"       Set {provider.upper()}_API_KEY or ~/.omni/providers/{provider}/config.yml, "
                    "or run `--jev --only 9.1` (the one scenario that needs no chat model).")
            return EXIT_PRECONDITION
        if has_key is False and a.dry_run:
            say_out(f"  WARNING        : '{provider}' has no API key; a real run would stop here")
        say_out()
        return EXIT_OK

    def scenarios(self) -> list[str]:
        if self.args.only:
            picked = {s.strip() for s in self.args.only.split(",") if s.strip()}
            known = SCENARIOS + EXTRA_SCENARIOS
            unknown = picked - set(known)
            if unknown:
                raise SystemExit(f"unknown scenario(s) {sorted(unknown)}; choose from {known}")
            for s in list(picked):
                picked.update(PREREQUISITES[s])
            return [s for s in known if s in picked]
        return list(SCENARIOS) + (list(EXTRA_SCENARIOS) if self.args.extras else [])

    # -- start / stop --------------------------------------------------------

    async def start(self) -> None:
        a = self.args
        self.tmp = Path(tempfile.mkdtemp(prefix="alfred-omni-smoke-"))
        if not a.shared_daemon:
            self._isolate()
        # Settings: global mutations on purpose, `decisions.choose` reads the module global.
        settings.data_dir = str(self.tmp / "data")
        settings.omni_turn_timeout = a.turn_timeout
        if self.jev_on:
            settings.jev_key_file = self.jev_key_path
        else:
            os.environ.pop("TYPESAFE_API_KEY", None)
            settings.jev_key_file = str(self.tmp / "no-jev-key")  # does not exist, on purpose

        from ..mcp.mock_ha import start_mock_ha

        self.mock_url, self.mock = start_mock_ha()
        self.seed_ambiguity()
        settings.ha_url = self.mock_url
        settings.ha_token = "mock"
        say_out(f"mock Home Assistant at {self.mock_url}")

        from .client import AlfredOmni

        self.bridge = AlfredOmni(REPO_ROOT, settings)
        await asyncio.wait_for(self.bridge.start(), START_TIMEOUT)
        self.mcp_local = self._patch_mcp_config()
        say_out(f"daemon connected; MCP config {self.mcp_local} points home_assistant at the mock")
        try:
            from omni.cli.connect import open_client

            self.observer_client = await open_client(spawn=False, client="alfred-smoke")
            self.observer = Observer(self.observer_client)
        except Exception as exc:
            say_out(f"note: journal observer unavailable ({type(exc).__name__}); paths will be blank")

    def seed_ambiguity(self) -> None:
        """Give the mock several healthy sensors, so a report that names none cannot be solved by
        reading the house (with an offline sensor in view the model just picks it: seen live once
        ``list_entities`` listed every domain), and the intake has to ask."""
        states = self.mock.states
        for entity_id, (name, state) in EXTRA_SENSORS.items():
            states[entity_id] = {
                "entity_id": entity_id,
                "state": state,
                "attributes": {"friendly_name": name},
                "last_changed": "2026-07-01T12:50:00+00:00",
            }

    def _isolate(self) -> None:
        assert self.tmp is not None
        home = self.tmp / "omni-home"
        home.mkdir()
        os.environ["OMNI_HOME"] = str(home)
        state = self.tmp / "state"
        state.mkdir()
        os.environ["LOCALAPPDATA"] = str(state)  # journal.db on Windows
        os.environ["XDG_STATE_HOME"] = str(state)  # journal.db elsewhere
        self.isolated = True

    def _patch_mcp_config(self) -> Path:
        from .library import local_mcp_config_path

        path = local_mcp_config_path(REPO_ROOT)
        config = json.loads(path.read_text(encoding="utf-8"))
        ha = config["mcpServers"]["home_assistant"]
        ha["env"] = {
            **ha.get("env", {}),
            "HA_URL": self.mock_url,
            "HA_TOKEN": "mock",
            # A path, never a key: where resolve_entity's Jev call finds its key file. A file that
            # does not exist makes it degrade instead of reaching Jev.
            "JEV_KEY_FILE": settings.jev_key_file,
        }
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.mcp_patched = True
        return path

    async def cleanup(self) -> None:
        """Stop everything this run started. Every step is independent and never raises."""
        if self.observer_client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.observer_client.close(), 5.0)
        if self.bridge is not None:
            await self._cancel_live_conversations()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.bridge.close(), 10.0)
        if self.mcp_patched:
            with contextlib.suppress(Exception):
                from .library import write_local_mcp_config

                write_local_mcp_config(REPO_ROOT)
        if self.mock is not None:
            with contextlib.suppress(Exception):
                self.mock.shutdown()
                self.mock.server_close()
        with contextlib.suppress(Exception):
            from .. import decisions

            decisions.close()
        if self.isolated:
            await asyncio.to_thread(self._stop_isolated_daemon)
        if self.tmp is not None:
            for _ in range(5):
                shutil.rmtree(self.tmp, ignore_errors=True)
                if not self.tmp.exists():
                    break
                await asyncio.sleep(0.5)

    async def _cancel_live_conversations(self) -> None:
        for cid in list(self.started_ids):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.bridge.forget(cid), 10.0)

    def _stop_isolated_daemon(self) -> None:
        """``omnid --stop`` for the private home; if it lingers, end the advertised pid."""
        from omni.cli.connect import stop_daemon
        from omni.codec.discovery import read_discovery

        home = Path(os.environ.get("OMNI_HOME", ""))
        if not self.tmp or self.tmp not in home.parents:
            return  # never touch a daemon outside our own temp home
        with contextlib.suppress(Exception):
            stop_daemon(quiet=True, timeout=30.0)
        record = None
        with contextlib.suppress(Exception):
            record = read_discovery()
        if record is not None and record.pid:
            say_out(f"note: daemon pid {record.pid} still advertised after --stop; ending it")
            with contextlib.suppress(Exception):
                os.kill(record.pid, 15)

    # -- driving -------------------------------------------------------------

    async def call(self, coro: Any) -> tuple[Any, float]:
        """Await one bridge call under a bound; returns ``(result, seconds)``."""
        t0 = time.monotonic()
        result = await asyncio.wait_for(coro, self.args.turn_timeout + CALL_SLACK)
        return result, time.monotonic() - t0

    async def path_of(self, conversation_id: str) -> tuple[str, list[Segment]]:
        if self.observer is None:
            return "(journal observer unavailable)", []
        try:
            segments = await self.observer.steps(conversation_id)
        except Exception as exc:
            return f"(journal unreadable: {type(exc).__name__})", []
        return " > ".join(s.render(conversation_id) for s in segments) or "(no new steps)", segments

    async def show(self, text: str, reply: Any, seconds: float) -> list[Segment]:
        """Print one exchange and return the journal segments it added."""
        say_out(f"  you    : {clip(text)}")
        say_out(f"  alfred : {clip(reply.answer) or '(nothing yet)'}")
        flags = [reply.state, f"conv={reply.conversation_id}"]
        if reply.new_conversation:
            flags.append("new_conversation")
        if reply.continued_from:
            flags.append(f"continued_from={reply.continued_from}")
        if reply.pending is not None:
            flags.append(f"ask={reply.pending.req_id} {reply.pending.options}")
        say_out(f"  reply  : {', '.join(flags)}  [{seconds:.1f}s]")
        rendered, segments = await self.path_of(reply.conversation_id)
        say_out(f"  path   : {rendered}")
        self.last_path = rendered
        return segments

    def track(self, reply: Any) -> None:
        if reply.conversation_id not in self.started_ids:
            self.started_ids.append(reply.conversation_id)

    async def exchange(self, text: str, coro: Any) -> tuple[Any, list[Segment]]:
        reply, seconds = await self.call(coro)
        self.track(reply)
        segments = await self.show(text, reply, seconds)
        return reply, segments

    async def answer(self, reply: Any, yes: bool) -> tuple[Any, list[Segment], str]:
        """Answer a pending ask: words through Jev when it is on, else the structured answer."""
        assert self.bridge is not None
        if self.jev_on:
            text = "yes please" if yes else "no, never mind"
            r, segs = await self.exchange(text, self.bridge.say(text, reply.conversation_id))
            return r, segs, text
        word = "allow" if yes else "deny"
        text = f"[answer_ask {word}]"
        r, segs = await self.exchange(
            text, self.bridge.answer_ask(reply.conversation_id, reply.pending.req_id, word)
        )
        return r, segs, text

    # -- scenarios -----------------------------------------------------------

    async def s91(self, sc: Scenario, ctx: dict[str, Any]) -> None:
        """9.1: a device command with no chat model, approved automatically."""
        text = "turn on the bedroom lamp"
        say_out(f"[9.1] {sc.title}")
        r1, segs1 = await self.exchange(text, self.bridge.say(text, None))
        sc.expect(r1.state == "awaiting_approval", f"first reply state is {r1.state!r}, want awaiting_approval")
        sc.expect("bedroom" in r1.answer.lower(), "the approval question does not name the Bedroom Lamp")
        if not sc.expect(r1.pending is not None, "no pending ask on the first reply"):
            return
        r2, segs2, _ = await self.answer(r1, yes=True)
        sc.expect(r2.state == "ended", f"second reply state is {r2.state!r}, want ended")
        ctx["r91"] = r2
        sc.expect(r2.conversation_id == r1.conversation_id, "the approval changed conversation id")
        sc.expect(" on" in r2.answer.lower(), "the final answer does not say the lamp is on")
        sc.expect(self.mock.states["light.bedroom_lamp"]["state"] == "on",
                  "the mock's light.bedroom_lamp is not on")
        nodes = {s.node for s in segs1 + segs2}
        sc.expect({"front", "device-action", "resolve", "say-act"} <= nodes,
                  f"path lacks the device nodes (saw {sorted(nodes)})")
        sc.expect(not (nodes & CHAT_NODES), f"a chat node ran: {sorted(nodes & CHAT_NODES)}")
        if self.jev_on:
            sc.expect("typesafe" in decided_providers(segs1 + segs2),
                      "no Decided came from typesafe (Jev) although --jev was given")

    async def late_path(self, conversation_id: str, seconds: float = 20.0) -> str:
        """Diagnostics for a reply that came back too early: what the journal shows over the next
        ``seconds`` (the run keeps going after a premature ``awaiting_user``), and the state the
        bridge then lists the conversation in."""
        parts: list[str] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            rendered, _ = await self.path_of(conversation_id)
            if rendered not in ("(no new steps)", "(journal observer unavailable)"):
                parts.append(rendered)
        listed = [c for c in await self.bridge.list_conversations() if c.conversation_id == conversation_id]
        state = listed[0].state if listed else "unlisted"
        text = f"later the journal shows: {' > '.join(parts) or '(nothing)'}; listed as {state}"
        say_out(f"  late   : {text}")
        return text

    async def ambiguous_intake(self, sc: Scenario, attempts: int = 1) -> Any | None:
        """Open a troubleshooting conversation whose intake holds the floor with a question.

        A model that reads the house and finds the culprit without asking is behaving legitimately,
        so ``attempts`` fresh conversations are tried before giving up; each attempt that did not
        ask is printed with its path, and a FAIL carries all of them. ``None`` when none held.
        """
        notes: list[str] = []
        for attempt in range(1, attempts + 1):
            text = AMBIGUOUS_OPENER
            r, segs = await self.exchange(text, self.bridge.say(text, None))
            front = [s for s in segs if s.node == "front" and s.run == r.conversation_id]
            routed = front[0].outcome if front else "no front node"
            if routed != "troubleshoot":
                notes.append(f"attempt {attempt}: front routed {routed!r}; path: {self.last_path}")
            elif r.state != "awaiting_user" or not r.answer.strip():
                notes.append(f"attempt {attempt}: intake did not ask (state {r.state!r}, answer "
                             f"{clip(r.answer, 120)!r}); path: {self.last_path}")
            else:
                if notes:
                    say_out(f"  note   : held the floor on attempt {attempt} ({len(notes)} earlier "
                            "attempt(s) resolved without asking)")
                return r
            say_out(f"  note   : {notes[-1].split('; path')[0]}")
        sc.failures.append("no conversation held the floor with a question: " + " | ".join(notes))
        return None

    async def s92(self, sc: Scenario, ctx: dict[str, Any]) -> None:
        """9.2: an ambiguous report makes intake ask; the answer is taken and the run goes on."""
        say_out(f"[9.2] {sc.title}")
        r1 = await self.ambiguous_intake(sc, attempts=2)
        if r1 is None:
            return
        text = INTAKE_ANSWER
        r2, segs = await self.exchange(text, self.bridge.say(text, r1.conversation_id))
        sc.expect(r2.conversation_id == r1.conversation_id,
                  f"the answer moved to conversation {r2.conversation_id!r}")
        sc.expect(bool(r2.answer.strip()), f"nothing was said after the answer; path: {self.last_path}")
        sc.expect(WENT_WRONG_MARK not in r2.answer.lower(),
                  f"Alfred reported an internal failure; path: {self.last_path}")
        intake = [s for s in segs if s.node == "intake"]
        identified = any(s.outcome == "identified" for s in intake)
        sc.expect(identified or r2.state == "awaiting_user",
                  f"the answer neither identified the device nor got another question "
                  f"(state {r2.state!r}); path: {self.last_path}")

    async def handoff(self, sc: Scenario, r0: Any, text: str) -> None:
        """Shared by 9.3 and 9.3x: ``text`` is said to the floor-holding conversation ``r0``; the
        bridge must answer from a NEW linked conversation that routed ``device`` and offers the
        physical ask, which is then refused."""
        r, segs = await self.exchange(text, self.bridge.say(text, r0.conversation_id))
        if not r.new_conversation:
            late = await self.late_path(r0.conversation_id)
            sc.failures.append(
                f"the handoff did not arrive with the reply (state {r.state!r}, same conversation); "
                f"path: {self.last_path}; {late}")
            return
        if WENT_WRONG_MARK in r.answer.lower():
            # Not a FAIL: the handoff worked. But the person is told "something went wrong" first.
            say_out("  WARN   : the handoff reply is prefaced with the bridge's went-wrong text "
                    "(the finished run said nothing; see docs/omni-jev-plan.md section 12, A7c)")
        sc.expect(r.continued_from == r0.conversation_id,
                  f"continued_from is {r.continued_from!r}, want {r0.conversation_id!r}")
        sc.expect(r.conversation_id != r0.conversation_id, "the handoff kept the old conversation id")
        front = [x for x in segs if x.node == "front" and x.run == r.conversation_id]
        sc.expect(front and front[0].outcome == "device",
                  f"the new run did not route device ({front[0].outcome if front else 'no front node'}); "
                  f"path: {self.last_path}")
        sc.expect(r.state == "awaiting_approval",
                  f"reply state is {r.state!r}, want awaiting_approval; path: {self.last_path}")
        sc.expect("coffee" in r.answer.lower(), "the approval question does not name the Coffee Maker")
        if r.pending is None:
            sc.failures.append(f"no pending ask after the handoff; path: {self.last_path}")
            return
        r2, _, _ = await self.answer(r, yes=False)
        sc.expect(r2.state == "ended", f"after the refusal state is {r2.state!r}, want ended")
        said = r2.answer.lower().replace("’", "'")
        sc.expect("didn't" in said or "did not" in said or "not done" in said,
                  f"Alfred did not say he left it undone (said {clip(r2.answer, 120)!r})")
        sc.expect(self.mock.states["switch.coffee_maker"]["state"] == "off",
                  "the coffee maker was switched on although the ask was refused")
        old = [c for c in await self.bridge.list_conversations() if c.conversation_id == r0.conversation_id]
        sc.expect(old and old[0].state == "ended", "the first conversation is not listed as ended")

    async def s93(self, sc: Scenario, ctx: dict[str, Any]) -> None:
        """9.3: a topic change inside a chat (a node that holds the floor) hands off."""
        say_out(f"[9.3] {sc.title}")
        notes: list[str] = []
        r0 = None
        for attempt in (1, 2):  # a model may still end the chat itself; that leaves nothing to hand off
            r, segs = await self.exchange(CHAT_OPENER, self.bridge.say(CHAT_OPENER, None))
            front = [x for x in segs if x.node == "front" and x.run == r.conversation_id]
            routed = front[0].outcome if front else "no front node"
            if routed != "chat":
                notes.append(f"attempt {attempt}: front routed {routed!r}; path: {self.last_path}")
            elif r.state != "awaiting_user" or not r.answer.strip():
                notes.append(f"attempt {attempt}: chat did not hold the floor (state {r.state!r}); "
                             f"path: {self.last_path}")
            else:
                r0 = r
                break
            say_out(f"  note   : {notes[-1].split('; path')[0]}")
        if r0 is None:
            sc.failures.append("no chat conversation held the floor: " + " | ".join(notes))
            return
        await self.handoff(sc, r0, TOPIC_CHANGE)

    async def s93x(self, sc: Scenario, ctx: dict[str, Any]) -> None:
        """9.3x (optional): the same handoff out of the troubleshooting intake."""
        say_out(f"[9.3x] {sc.title}")
        r0 = await self.ambiguous_intake(sc, attempts=3)
        if r0 is None:
            return
        await self.handoff(sc, r0, "actually, forget that - turn on the coffee maker")

    async def s94(self, sc: Scenario, ctx: dict[str, Any]) -> None:
        """9.4: an ended conversation (9.1's) is listed, readable, and can be continued."""
        cid = ctx["r91"].conversation_id
        say_out(f"[9.4] {sc.title}")
        listing = await self.bridge.list_conversations()
        say_out(f"  list   : {len(listing)} conversation(s): "
                + ", ".join(f"{c.conversation_id}={c.state}" for c in listing))
        sc.expect(any(c.conversation_id == cid and c.state == "ended" for c in listing),
                  f"{cid} is not listed as ended")
        transcript = await self.bridge.transcript(cid)
        users = [e.text for e in transcript.entries if e.role == "user"]
        say_out(f"  read   : transcript of {cid}: {len(transcript.entries)} entries"
                + (f", first user line {clip(users[0], 80)!r}" if users else ""))
        sc.expect(bool(users) and "bedroom lamp" in users[0].lower(),
                  "the transcript lacks the first user line")
        # Wording-independent: only the plumbing is asserted (new run, linked, earlier text carried),
        # not how the model routes or phrases the follow-up.
        text = "that lamp keeps going offline"
        r, segs = await self.exchange(text, self.bridge.continue_from(cid, text))
        sc.expect(r.new_conversation, "reply is not marked new_conversation")
        sc.expect(r.continued_from == cid, f"continued_from is {r.continued_from!r}, want {cid!r}")
        sc.expect(r.conversation_id != cid, "the continue reused the earlier conversation id")
        sc.expect(r.state in ("awaiting_user", "awaiting_approval", "ended"),
                  f"reply state is {r.state!r}; path: {self.last_path}")
        sc.expect(bool(r.answer.strip()), f"nothing was said; path: {self.last_path}")
        sc.expect(WENT_WRONG_MARK not in r.answer.lower(),
                  f"Alfred reported an internal failure; path: {self.last_path}")
        if self.observer is not None:
            earlier = (await self.observer.inputs(r.conversation_id)).get("earlier", "")
            say_out(f"  earlier: inputs.earlier is {len(str(earlier))} chars")
            sc.expect("bedroom lamp" in str(earlier).lower(),
                      "inputs.earlier does not carry the earlier conversation")

    async def run_scenarios(self) -> list[Scenario]:
        titles = {
            "9.1": "device command, no chat model: lamp on after an automatic yes",
            "9.2": "troubleshooting: ambiguous report, intake asks, the answer is taken",
            "9.3": "topic change inside a chat: hand off to a new conversation (refused ask)",
            "9.3x": "optional: topic change inside the troubleshooting intake",
            "9.4": "load an earlier (ended) conversation and continue it",
        }
        funcs = {"9.1": self.s91, "9.2": self.s92, "9.3": self.s93, "9.4": self.s94,
                 "9.3x": self.s93x}
        results: list[Scenario] = []
        ctx: dict[str, Any] = {}
        failed: set[str] = set()
        for name in self.scenarios():
            sc = Scenario(name, titles[name])
            missing = [p for p in PREREQUISITES[name] if p in failed]
            if missing:
                sc.failures.append(f"not run: prerequisite scenario {', '.join(missing)} failed")
                sc.finish()
            else:
                try:
                    await funcs[name](sc, ctx)
                except asyncio.TimeoutError:
                    sc.failures.append("a bridge call timed out (the daemon or a model is stuck)")
                except Exception as exc:
                    sc.failures.append(f"{type(exc).__name__}: {clip(exc, 300)}")
                    logger.debug("scenario %s raised", name, exc_info=True)
                sc.finish()
            if not sc.ok:
                failed.add(name)
            results.append(sc)
        return results

    # -- the dry run ---------------------------------------------------------

    async def dry_checks(self) -> bool:
        """Everything short of a model call: library, constraints, MCP wiring, daemon."""
        ok = True

        def check(label: str, passed: bool, detail: str = "") -> None:
            nonlocal ok
            ok = ok and passed
            say_out(f"  [{'ok' if passed else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))

        say_out("dry run checks:")
        from .library import load_library

        library, graphs = load_library(REPO_ROOT, settings)
        injected = total = 0
        for graph in graphs.values():
            for node in graph.nodes.values():
                if node.kind in ("agent", "tool"):
                    total += 1
                    if node.constraints.get("mcp_config_path") and node.constraints.get("cwd"):
                        injected += 1
        check("graph library", settings.omni_front_graph in graphs and len(graphs) >= 4,
              f"{len(graphs)} graph(s): {', '.join(sorted(graphs))}")
        check("constraints injected", total > 0 and injected == total,
              f"{injected}/{total} agent+tool nodes carry cwd and mcp_config_path")

        path = self.mcp_local
        config = json.loads(path.read_text(encoding="utf-8")) if path else {}
        ha_env = config.get("mcpServers", {}).get("home_assistant", {}).get("env", {})
        check("mcp.local.json", ha_env.get("HA_URL") == self.mock_url,
              f"home_assistant env has HA_URL={ha_env.get('HA_URL')} and JEV_KEY_FILE"
              f"{' (the real key file path)' if self.jev_on else ' (a file that does not exist)'}")

        sensors = sorted(k for k in self.mock.states if k.startswith("binary_sensor."))
        check("ambiguity seeded", len(sensors) >= 3, f"sensors in the mock: {', '.join(sensors)}")
        check("mock HA reachable", await asyncio.to_thread(self._mock_ok), self.mock_url)
        check("daemon healthy", await self.bridge.healthy(), "list_runs answered")
        check("journal observer", self.observer is not None, "second client attached")

        # The MCP server, started exactly as the daemon would from the patched file.
        ok_mcp, detail = await asyncio.to_thread(self._probe_mcp)
        check("home_assistant MCP server -> mock", ok_mcp, detail)
        return ok

    def _mock_ok(self) -> bool:
        import urllib.request

        req = urllib.request.Request(self.mock_url + "/api/", headers={"Authorization": "Bearer mock"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - 127.0.0.1 mock
            return resp.status == 200

    def _probe_mcp(self) -> tuple[bool, str]:
        from omni.driver.tools.mcp.client import McpClient
        from omni.driver.tools.mcp.config import load_mcp_config_from_path

        assert self.mcp_local is not None
        configs = [c for c in load_mcp_config_from_path(self.mcp_local) if c.name == "home_assistant"]
        if not configs:
            return False, "no home_assistant entry in the generated config"
        client = McpClient(configs)
        try:
            client.start()
            result = client.call_tool("home_assistant", "list_entities", {})
            text = McpClient.text_of(result)
            good = not McpClient.failed(result) and "light.bedroom_lamp" in text
            return good, "list_entities returned the mock's lamp" if good else clip(text, 160)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {clip(exc, 160)}"
        finally:
            with contextlib.suppress(Exception):
                client.close()


# ── entry point ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ai_server.omni_bridge.smoke",
        description="Live end-to-end smoke of the omni bridge against the mock Home Assistant.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="build and wire everything, connect to a daemon, run no conversation, stop")
    p.add_argument("--jev", action="store_true",
                   help="use real Jev for decide nodes: the key file is handed to the spawned "
                        "daemon in-process (never printed). Without it decide nodes use the LLM path.")
    p.add_argument("--jev-key-file", default=None, metavar="PATH",
                   help="the Jev key file (default: settings.jev_key_file; in a worktree pass the "
                        "main checkout's jev.env)")
    p.add_argument("--extras", action="store_true",
                   help="also run the optional scenario 9.3x (handoff out of the troubleshooting intake)")
    p.add_argument("--only", default=None, metavar="9.1,9.3",
                   help="comma-separated scenarios (prerequisites are added automatically)")
    p.add_argument("--timeout", type=float, default=900.0, help="hard overall timeout, seconds")
    p.add_argument("--turn-timeout", type=float, default=90.0,
                   help="seconds one bridge turn may run (settings.omni_turn_timeout)")
    p.add_argument("--shared-daemon", action="store_true",
                   help="use the daemon for your normal OMNI_HOME instead of a private one "
                        "(left running; the bridge cancels unknown client:graph runs on start)")
    p.add_argument("-v", "--verbose", action="store_true", help="bridge INFO logging")
    return p


async def run(args: argparse.Namespace, harness: Harness) -> int:
    await harness.start()
    if args.dry_run:
        ok = await harness.dry_checks()
        say_out()
        say_out("DRY RUN " + ("PASS" if ok else "FAIL"))
        return EXIT_OK if ok else EXIT_FAIL
    results = await harness.run_scenarios()
    say_out("summary:")
    for sc in results:
        say_out(f"  {sc.name}  {'PASS' if sc.ok else 'FAIL'}  {sc.elapsed:.1f}s  {sc.title}")
    failed = [sc.name for sc in results if not sc.ok]
    say_out("RESULT " + ("PASS" if not failed else f"FAIL ({', '.join(failed)})"))
    return EXIT_OK if not failed else EXIT_FAIL


async def amain(args: argparse.Namespace) -> int:
    harness = Harness(args)
    code = harness.preflight()
    if code != EXIT_OK:
        return code
    try:
        return await asyncio.wait_for(run(args, harness), args.timeout)
    except asyncio.TimeoutError:
        say_out(f"FAIL: overall timeout of {args.timeout:g}s reached; stopping everything")
        return EXIT_TIMEOUT
    except Exception as exc:
        say_out(f"FAIL: {type(exc).__name__}: {clip(exc, 300)}")
        logger.debug("smoke failed", exc_info=True)
        return EXIT_FAIL
    finally:
        await harness.cleanup()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
