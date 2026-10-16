"""A tiny mock of the Home Assistant REST API (omni-jev plan, section 6.4).

Shared by the unit tests, ``ai_server/mcp/smoke_test.py`` and the live smoke
test, so all three drive the same fixture. It speaks just the endpoints
``HomeAssistantIntegration`` uses: ``GET /api/``, ``GET /api/states``,
``GET /api/states/<entity_id>`` and ``POST /api/services/<domain>/<service>``.

State is **per server instance** (``server.states``), not module-global, so one
test's ``turn_on`` never leaks into the next. ``POST`` handling really changes
state (``turn_on`` / ``turn_off`` / ``toggle`` / ``set_temperature``), which is
what makes ``apply`` observable. Like real Home Assistant, a service call on an
unknown entity answers ``200`` with an empty list and changes nothing.
"""

from __future__ import annotations

import copy
import http.server
import json
import threading
from typing import Any

#: The mock house. ``light`` / ``switch`` / ``climate`` are in the integration's
#: ``discover_devices`` domains; ``binary_sensor`` only shows up for ``status``.
MOCK_STATES: dict[str, dict[str, Any]] = {
    "light.bedroom_lamp": {
        "entity_id": "light.bedroom_lamp",
        "state": "off",
        "attributes": {"friendly_name": "Bedroom Lamp"},
        "last_changed": "2026-07-01T14:02:00+00:00",
    },
    "light.living_room_lamp": {
        "entity_id": "light.living_room_lamp",
        "state": "off",
        "attributes": {"friendly_name": "Living Room Lamp"},
        "last_changed": "2026-07-01T09:30:00+00:00",
    },
    "switch.coffee_maker": {
        "entity_id": "switch.coffee_maker",
        "state": "off",
        "attributes": {"friendly_name": "Coffee Maker"},
        "last_changed": "2026-07-01T07:15:00+00:00",
    },
    "climate.living_room": {
        "entity_id": "climate.living_room",
        "state": "heat",
        "attributes": {"friendly_name": "Living Room Thermostat", "temperature": 68},
        "last_changed": "2026-07-01T06:00:00+00:00",
    },
    "binary_sensor.hallway_motion": {
        "entity_id": "binary_sensor.hallway_motion",
        "state": "off",
        "attributes": {"friendly_name": "Hallway Motion"},
        "last_changed": "2026-07-01T12:45:00+00:00",
    },
}


class MockHaServer(http.server.HTTPServer):
    """An ``HTTPServer`` that owns its own (deep-copied) entity states."""

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _MockHaHandler)
        self.states: dict[str, dict[str, Any]] = copy.deepcopy(MOCK_STATES)
        #: Every service call received, as ``(domain, service, body)``.
        self.calls: list[tuple[str, str, dict[str, Any]]] = []


class _MockHaHandler(http.server.BaseHTTPRequestHandler):
    server: MockHaServer

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        pass  # keep test and smoke output readable

    def _json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - stdlib method name
        states = self.server.states
        if self.path == "/api/":
            self._json(200, {"message": "API running (mock)."})
        elif self.path == "/api/states":
            self._json(200, list(states.values()))
        elif self.path.startswith("/api/states/"):
            state = states.get(self.path.removeprefix("/api/states/"))
            if state is None:
                self._json(404, {"message": "not found"})
            else:
                self._json(200, state)
        else:
            self._json(404, {"message": "not found"})

    def do_POST(self):  # noqa: N802 - stdlib method name
        parts = self.path.split("/")  # ['', 'api', 'services', domain, service]
        if len(parts) != 5 or parts[:3] != ["", "api", "services"]:
            self._json(404, {"message": "not found"})
            return
        domain, service = parts[3], parts[4]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append((domain, service, body))
        entity = self.server.states.get(body.get("entity_id", ""))
        if entity is None or not entity["entity_id"].startswith(f"{domain}."):
            self._json(200, [])
            return
        if service == "turn_on":
            entity["state"] = "on"
        elif service == "turn_off":
            entity["state"] = "off"
        elif service == "toggle":
            entity["state"] = "off" if entity["state"] == "on" else "on"
        elif service == "set_temperature" and "temperature" in body:
            entity["attributes"]["temperature"] = body["temperature"]
        self._json(200, [entity])


def start_mock_ha() -> tuple[str, MockHaServer]:
    """Start the mock on a free localhost port in a daemon thread.

    Returns ``(url, server)``. Stop it with ``server.shutdown()`` then
    ``server.server_close()``; inspect or seed ``server.states`` freely.
    """
    server = MockHaServer(("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}", server
