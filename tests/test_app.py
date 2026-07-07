"""The unified `skrooge-firefly` front end: --import FILE / --export FILE."""

import pytest

from skrooge2firefly.app import build_parser, main


def test_requires_exactly_one_mode():
    # Neither --import nor --export → argparse error (exit 2 via SystemExit).
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_import_and_export_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--import", "a.sqlite", "--export", "b.qif"])


def test_import_dispatches_to_import_flow(monkeypatch):
    calls = {}

    def fake_import(argv, **kw):
        calls["argv"] = argv
        return 0

    monkeypatch.setattr("skrooge2firefly.app.import_main", fake_import)
    monkeypatch.setattr("skrooge2firefly.app.export_main", lambda *a, **k: 99)
    rc = main(
        ["--import", "My.sqlite", "--dry-run", "--url", "https://f.example", "--concurrency", "4"]
    )
    assert rc == 0
    argv = calls["argv"]
    assert "--input" in argv and "My.sqlite" in argv
    assert "--target" in argv and argv[argv.index("--target") + 1] == "api"
    assert "--dry-run" in argv
    assert "--url" in argv and "https://f.example" in argv
    assert "--concurrency" in argv and "4" in argv


def test_export_dispatches_to_export_flow(monkeypatch):
    calls = {}

    def fake_export(argv, **kw):
        calls["argv"] = argv
        return 0

    monkeypatch.setattr("skrooge2firefly.app.export_main", fake_export)
    monkeypatch.setattr("skrooge2firefly.app.import_main", lambda *a, **k: 99)
    rc = main(["--export", "backup.sqlite", "--template", "T.sqlite"])
    assert rc == 0
    argv = calls["argv"]
    assert "--output" in argv and "backup.sqlite" in argv
    assert "--template" in argv and "T.sqlite" in argv


def test_export_only_flag_rejected_in_import_mode():
    # --format is export-only; using it with --import is an error.
    rc = main(["--import", "a.sqlite", "--format", "qif"])
    assert rc == 2


def test_import_only_flag_rejected_in_export_mode():
    rc = main(["--export", "a.qif", "--update"])
    assert rc == 2


def test_orphans_and_decisions_are_forwarded(monkeypatch):
    calls = {}

    def _cap(argv, **k):
        calls["argv"] = argv
        return 0

    monkeypatch.setattr("skrooge2firefly.app.import_main", _cap)
    rc = main(["--import", "a.sqlite", "--update", "--orphans", "delete", "--decisions", "d.json"])
    assert rc == 0
    argv = calls["argv"]
    assert "--orphans" in argv and argv[argv.index("--orphans") + 1] == "delete"
    assert "--decisions" in argv and argv[argv.index("--decisions") + 1] == "d.json"


def test_orphans_flag_rejected_in_export_mode():
    rc = main(["--export", "a.qif", "--orphans", "delete"])
    assert rc == 2


def test_csv_target_passes_output_dir(monkeypatch, tmp_path):
    calls = {}

    def _cap(argv, **k):
        calls["argv"] = argv
        return 0

    monkeypatch.setattr("skrooge2firefly.app.import_main", _cap)
    rc = main(["--import", "a.sqlite", "--target", "csv", "--csv-out", str(tmp_path / "o")])
    assert rc == 0
    argv = calls["argv"]
    assert argv[argv.index("--target") + 1] == "csv"
    assert "--output" in argv and str(tmp_path / "o") in argv
