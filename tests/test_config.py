from pathlib import Path

import pytest

from skrooge2firefly.config import ConfigError, Settings
from skrooge2firefly.config import load_dotenv as real_load_dotenv


def _resolve(**kwargs):
    defaults = {
        "url": None,
        "token": None,
        "input_path": None,
        "default_url": "https://default.example",
        "default_input": "default.sqlite",
    }
    defaults.update(kwargs)
    return Settings.resolve(**defaults)


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("FIREFLY_TOKEN", "tok")
    monkeypatch.delenv("FIREFLY_URL", raising=False)
    monkeypatch.delenv("SKROOGE_FILE", raising=False)
    s = _resolve()
    assert s.token == "tok"
    assert s.url == "https://default.example"
    assert s.input == Path("default.sqlite")


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("FIREFLY_TOKEN", "envtok")
    monkeypatch.setenv("FIREFLY_URL", "https://env.example")
    monkeypatch.setenv("SKROOGE_FILE", "env.sqlite")
    s = _resolve(url="https://cli.example", token="clitok", input_path=Path("cli.sqlite"))
    assert s.url == "https://cli.example"
    assert s.token == "clitok"
    assert s.input == Path("cli.sqlite")


def test_input_from_env(monkeypatch):
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    monkeypatch.setenv("SKROOGE_FILE", "from-env.sqlite")
    s = _resolve()
    assert s.input == Path("from-env.sqlite")


def test_missing_token_for_api_raises(monkeypatch):
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    with pytest.raises(ConfigError):
        _resolve().require_token()


def test_env_file_found_from_working_directory(monkeypatch, tmp_path):
    """A .env in the cwd must be found even though config.py lives elsewhere.

    ``load_dotenv()`` with no argument searches upward from the *calling
    source file*, which resolves into site-packages for an installed CLI.
    ``resolve`` must instead search from the process's working directory.
    """
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    monkeypatch.delenv("FIREFLY_URL", raising=False)
    monkeypatch.delenv("SKROOGE_FILE", raising=False)
    # Restore the real load_dotenv; conftest's autouse fixture stubs it out
    # for hermeticity in other tests.
    monkeypatch.setattr("skrooge2firefly.config.load_dotenv", real_load_dotenv)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("FIREFLY_TOKEN=xyz\n")

    s = _resolve()
    assert s.token == "xyz"
