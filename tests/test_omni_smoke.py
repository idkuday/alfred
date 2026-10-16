"""The live smoke script's offline parts (omni-jev A7): option handling and rendering.

The conversations themselves are driven by the coordinator against a real daemon
(``python -m ai_server.omni_bridge.smoke``); nothing here starts one.
"""

import pytest

pytest.importorskip("omni")

from ai_server.omni_bridge import smoke  # noqa: E402


def _harness(*argv: str) -> smoke.Harness:
    return smoke.Harness(smoke.build_parser().parse_args(list(argv)))


def test_only_pulls_in_prerequisites():
    assert _harness("--only", "9.4").scenarios() == ["9.1", "9.4"]
    assert _harness("--only", "9.1").scenarios() == ["9.1"]
    assert _harness().scenarios() == list(smoke.SCENARIOS)


def test_unknown_scenario_is_rejected():
    with pytest.raises(SystemExit):
        _harness("--only", "9.9").scenarios()


def test_segment_render_marks_child_runs_and_open_nodes():
    done = smoke.Segment("front", "run-1", ["decided device 0.97 typesafe/jev-latest 80ms"], "device")
    child = smoke.Segment("intake", "run-2")
    assert done.render("run-1") == "front[decided device 0.97 typesafe/jev-latest 80ms]=device"
    assert child.render("run-1") == "run-2:intake…"
    assert smoke.decided_providers([done, child]) == {"typesafe"}


def test_jev_without_a_key_file_is_a_precondition_failure(tmp_path, capsys):
    h = _harness("--jev", "--jev-key-file", str(tmp_path / "missing.key"))
    assert h.preflight() == smoke.EXIT_PRECONDITION
    out = capsys.readouterr().out
    assert "NO KEY" in out


def test_jev_key_is_never_printed(tmp_path, capsys):
    key = tmp_path / "jev.key"
    key.write_text("sk-secret-value-123", encoding="utf-8")
    h = _harness("--jev", "--jev-key-file", str(key), "--dry-run")
    h.preflight()
    assert "sk-secret-value-123" not in capsys.readouterr().out


def test_extras_are_opt_in():
    assert "9.3x" not in _harness().scenarios()
    assert _harness("--extras").scenarios() == [*smoke.SCENARIOS, "9.3x"]
    assert _harness("--only", "9.3x").scenarios() == ["9.3x"]
