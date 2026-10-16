"""
Path defaults must not depend on the process cwd (omni-jev plan J21).

Before A1, ``plugins_dir`` was the relative string ``"plugins"``, so Alfred started
anywhere but the repo root loaded zero plugins.
"""
from pathlib import Path

from ai_server.config import REPO_ROOT, settings
from ai_server.plugins import PluginManager


def test_plugins_load_from_any_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    manager = PluginManager()
    manager.load_plugins()
    assert manager.list_integrations(), "no plugins loaded from a non-repo cwd"


def test_plugins_dir_default_is_absolute_package_dir():
    assert Path(settings.plugins_dir).is_absolute()
    assert Path(settings.plugins_dir) == REPO_ROOT / "ai_server" / "plugins"


def test_jev_key_file_default_ends_with_jev_env():
    assert Path(settings.jev_key_file).name == "jev.env"


def test_data_dir_default_is_under_repo_root():
    assert Path(settings.data_dir) == REPO_ROOT / "data"
