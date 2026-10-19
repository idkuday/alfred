"""Asks: turning a kernel ``Request`` into a question, and the reply into an answer
(omni-jev plan J13/J14, section 7.6).

A physical tool call (permission) or a ``human`` node (approval) parks the run on a
``Request``. Alfred's UI only sends text, so the bridge renders the request as a sentence,
returns it as the reply, and reads the person's *next message* as the answer, with a Jev
``choice`` (``decide``) deciding what it means. Below the confidence threshold Alfred asks
once more, then denies: never act on an ambiguous yes.

Pure except for ``interpret``, which awaits the injected ``decide`` (J5: in production
``ai_server.decisions.choose``; tests pass a fake).

Decision words (plan section 12): Alfred's options are ``allow``/``deny``, but the kernel's
permission gate reads ``once``/``always``/``deny`` (``decision_from_response``; an unknown
word denies). ``allow`` therefore maps to ``once`` - never ``always``, because a physical
call is approved per call (D64). A sandbox ask uses ``allow``/``deny`` as-is. An approval
answers with the request's own outcome names, which the walker matches exactly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from omni.graph.schema import Node
from omni.kernel.events import Request, RequestKind

from .models import PendingAsk

logger = logging.getLogger(__name__)

#: Confidence a Jev answer must reach for Alfred to act on it (J13).
ASK_THRESHOLD = 0.75

#: Cap on one shown value in an approval question (section 7.6).
MAX_SHOW_CHARS = 400

HA_APPLY = "mcp__home_assistant__apply"

#: ``decide(question, options, state, *, threshold) -> (answer | None, confidence)``.
Decide = Callable[..., Awaitable[tuple[str | None, float]]]

#: ``lookup(stream_id, request) -> Node | None``: the graph node that raised ``request``
#: (a ``human`` node, for approvals). The client resolves it from the run's fold.
NodeLookup = Callable[[str, Request], Node | None]

_PERMISSION_OPTIONS = {
    "allow": "yes, go ahead, do it, sure, please",
    "deny": "no, stop, don't, cancel, or a different request instead",
}

REASK_PERMISSION = "Sorry — was that a yes or a no?"
NOT_DONE = "Okay, I didn't do that."
GAVE_UP_PERMISSION = "I wasn't sure what you meant, so I didn't do it."


def short_tool_name(tool: str) -> str:
    """``mcp__server__tool`` -> ``tool``; any other name is returned unchanged."""
    return tool.rsplit("__", 1)[-1] if tool.startswith("mcp__") and "__" in tool[5:] else tool


def _json_object(value: Any) -> dict[str, Any]:
    """``value`` as a dict if it is one, or a JSON string holding one; else ``{}``."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def _permission_question(payload: Mapping[str, Any]) -> str:
    tool = str(payload.get("tool") or payload.get("op") or "")
    args = _json_object(payload.get("args"))
    short = short_tool_name(tool) or "a tool"
    if tool == HA_APPLY:
        name = str(args.get("entity_name") or args.get("entity_id") or "").strip()
        verb = str(args.get("action") or "").replace("-", " ").replace("_", " ").strip()
        if name and verb:
            return f"Shall I {verb} {name}?"
    entity_name = args.get("entity_name")
    if isinstance(entity_name, str) and entity_name.strip():
        return f"Shall I run {short} on {entity_name.strip()}?"
    return f"Alfred wants to use {short}. Allow it?"


def _shown(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= MAX_SHOW_CHARS else text[: MAX_SHOW_CHARS - 1] + "…"


def _option_ids(raw: Any) -> list[str]:
    """Option names from a payload's ``options``: plain strings or ``{id, label}`` objects."""
    if not isinstance(raw, (list, tuple)):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            names.append(item["id"])
    return names


def render(
    request: Request, stream_id: str, node_lookup: NodeLookup | None = None
) -> PendingAsk:
    """The question Alfred puts for ``request``, which arrived on ``stream_id``.

    * permission: ``Shall I turn on Bedroom Lamp?`` for the Home Assistant ``apply`` tool,
      ``Shall I run <tool> on <entity_name>?`` for any tool with an ``entity_name`` arg,
      else ``Alfred wants to use <tool>. Allow it?``; options ``["allow", "deny"]``.
    * approval (and ``choice``): the node's prompt, each shown value (trimmed to 400
      characters), then ``(a / b)``; options are the request's outcomes.
    * elicit: the server's message; no options, the reply text is the data.
    * secret: never taken over chat; the only option is ``deny``.
    """
    payload = dict(request.payload)
    kind = request.kind

    if kind in (RequestKind.PERMISSION, RequestKind.SANDBOX):
        offered = _option_ids(payload.get("options"))
        if kind is RequestKind.SANDBOX:
            allow_word = "allow"
        else:
            allow_word = next((o for o in offered if o not in ("deny", "always")), "once")
        question = (
            _permission_question(payload)
            if kind is RequestKind.PERMISSION
            else f"Alfred wants to access {payload.get('path') or 'a path'}. Allow it?"
        )
        return PendingAsk(
            req_id=request.req_id,
            stream_id=stream_id,
            kind="permission",
            question=question,
            options=["allow", "deny"],
            respond_with={"allow": allow_word, "deny": "deny"},
        )

    if kind is RequestKind.SECRET:
        return PendingAsk(
            req_id=request.req_id,
            stream_id=stream_id,
            kind="permission",
            question=(
                "Something asked for a secret, and I won't take those over chat. "
                "Say no to decline."
            ),
            options=["deny"],
            respond_with={"deny": "deny"},
        )

    if kind is RequestKind.ELICIT:
        message = str(payload.get("message") or payload.get("prompt") or "I need some information.")
        return PendingAsk(
            req_id=request.req_id,
            stream_id=stream_id,
            kind="elicit",
            question=message,
            options=[],
        )

    # APPROVAL (a human node) and CHOICE: the request's own outcomes.
    node = node_lookup(stream_id, request) if node_lookup is not None else None
    options = _option_ids(payload.get("options"))
    if not options and node is not None:
        options = [o for o in node.declared if o not in ("error", "timeout")]
    prompt = str(payload.get("prompt") or payload.get("message") or "").strip()
    lines = [prompt] if prompt else []
    show = payload.get("show")
    if isinstance(show, Mapping):
        lines.extend(f"{path}: {_shown(value)}" for path, value in show.items())
    if options:
        lines.append("(" + " / ".join(options) + ")")
    descriptions: dict[str, str] = {}
    fallback: str | None = options[-1] if options else None
    if node is not None:
        descriptions = {
            name: node.outcome_descriptions[name]
            for name in options
            if node.outcome_descriptions.get(name)
        }
        if node.on_timeout and node.on_timeout in options:
            fallback = node.on_timeout
    return PendingAsk(
        req_id=request.req_id,
        stream_id=stream_id,
        kind="approval",
        question="\n".join(lines) or "Do you approve?",
        options=options,
        descriptions=descriptions,
        fallback=fallback,
    )


async def interpret(pending: PendingAsk, text: str, decide: Decide) -> str | None:
    """Which of ``pending.options`` the person's ``text`` chooses, or ``None`` if unsure.

    ``decide`` is the injected Jev ``choice`` (J13). ``None`` covers every kind of "I could
    not tell": low confidence, no model, an exception, an answer outside the options.
    An elicit ask is answered by any non-empty text (``"accept"``); an ask with a single
    option needs no model.
    """
    if pending.kind == "elicit":
        return "accept" if text.strip() else None
    if len(pending.options) == 1:
        return pending.options[0]
    if not pending.options:
        return None
    if pending.kind == "permission":
        question = "Does this reply give permission?"
        choices = dict(_PERMISSION_OPTIONS)
    else:
        question = "Which outcome does this reply choose?"
        choices = {name: pending.descriptions.get(name) or name for name in pending.options}
    try:
        answer, confidence = await decide(
            question,
            choices,
            {"question": pending.question, "reply": text},
            threshold=ASK_THRESHOLD,
        )
    except Exception as exc:  # decide is meant not to raise; an ask must not crash on it
        logger.warning("ask interpretation failed: %s", type(exc).__name__)
        return None
    if answer is None or answer not in pending.options:
        logger.debug("ask %s: unsure (confidence %.2f)", pending.req_id, confidence)
        return None
    return answer


def decision_word(pending: PendingAsk, option: str) -> str:
    """The word the kernel's ``respond`` should carry for ``option`` (``allow`` -> ``once``)."""
    return pending.respond_with.get(option, option)


def response_data(pending: PendingAsk, text: str) -> dict[str, Any]:
    """The structured half of a ``respond`` (only an elicit ask carries any)."""
    return {"text": text.strip()} if pending.kind == "elicit" else {}


@dataclass(frozen=True, slots=True)
class AskStep:
    """What the client does after interpreting one reply (section 7.6).

    ``option`` is set when the ask is resolved: send ``word`` (and ``data``) with
    ``respond``. ``pending`` is set when it is not, and is the ask to keep parked (with
    ``reasks`` incremented). ``say`` is anything Alfred should add to its reply.
    """

    option: str | None = None
    word: str | None = None
    data: Mapping[str, Any] | None = None
    pending: PendingAsk | None = None
    say: str = ""


def step(pending: PendingAsk, answer: str | None, text: str = "") -> AskStep:
    """Decide what happens to ``pending`` given :func:`interpret`'s ``answer``.

    * a clear answer resolves it (a refused permission adds "Okay, I didn't do that.", which
      is also what J14 needs when a message that was not an answer resolved as ``deny``);
    * no clear answer the first time re-asks;
    * no clear answer the second time resolves it as ``deny`` (permission) or the node's
      ``on_timeout`` outcome / last outcome (approval), and says so.
    """
    if answer is not None:
        say = NOT_DONE if pending.kind == "permission" and answer == "deny" else ""
        return AskStep(
            option=answer,
            word=decision_word(pending, answer),
            data=response_data(pending, text),
            say=say,
        )
    if pending.reasks < 1:
        again = pending.model_copy(update={"reasks": pending.reasks + 1})
        if pending.kind == "permission":
            say = REASK_PERMISSION
        elif pending.kind == "elicit":
            say = pending.question
        else:
            say = "Sorry — which did you mean? (" + " / ".join(pending.options) + ")"
        return AskStep(pending=again, say=say)
    if pending.kind == "permission":
        return AskStep(option="deny", word=decision_word(pending, "deny"), say=GAVE_UP_PERMISSION)
    if pending.kind == "elicit":
        return AskStep(option="decline", word="decline", say="Okay, I'll leave that.")
    fallback = pending.fallback or (pending.options[-1] if pending.options else "")
    return AskStep(
        option=fallback,
        word=decision_word(pending, fallback),
        say=f"I wasn't sure what you meant, so I chose \"{fallback}\".",
    )
