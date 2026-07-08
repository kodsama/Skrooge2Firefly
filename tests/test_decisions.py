from pathlib import Path

from skrooge2firefly.writers.decisions import load, save


def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    p = tmp_path / "d.json"
    save(p, {"skrooge:op:1": "delete", "rec:Netflix": "ignore"})
    assert load(p) == {"skrooge:op:1": "delete", "rec:Netflix": "ignore"}


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == {}
