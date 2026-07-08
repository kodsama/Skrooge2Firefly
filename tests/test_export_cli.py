from decimal import Decimal
from pathlib import Path

from skrooge2firefly.export_cli import build_parser, infer_format, main


def test_infer_format():
    assert infer_format(Path("x.qif"), None) == "qif"
    assert infer_format(Path("x.sqlite"), None) == "sqlite"
    assert infer_format(Path("x.skg"), None) == "sqlite"
    assert infer_format(Path("x.qif"), "sqlite") == "sqlite"


def test_parser_defaults():
    args = build_parser("https://cash.example").parse_args([])
    assert args.output == Path("firefly-export.qif")
    assert args.format is None and args.template is None


def _pulled_data():
    from skrooge2firefly.export.puller import PulledData
    from skrooge2firefly.model.entities import Account, Split, Transaction

    return PulledData(
        [Account("firefly:acct:1", "Checking", "asset", "defaultAsset", "SEK", None, None, None)],
        [
            Transaction(
                "x",
                "withdrawal",
                "2020-05-01",
                [Split(Decimal("12.50"), "SEK", "Checking", "ICA")],
            )
        ],
        [],
    )


def test_export_cli_qif_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREFLY_TOKEN", "tok")
    monkeypatch.setattr(
        "skrooge2firefly.export_cli._connect_and_pull", lambda a, s: (_pulled_data(), {})
    )
    out = tmp_path / "backup.qif"
    assert main(["--output", str(out)]) == 0
    assert "T-12.50" in out.read_text()


def test_export_cli_sqlite_end_to_end(monkeypatch, tmp_path, skrooge_template):
    monkeypatch.setenv("FIREFLY_TOKEN", "tok")
    monkeypatch.setattr(
        "skrooge2firefly.export_cli._connect_and_pull", lambda a, s: (_pulled_data(), {})
    )
    out = tmp_path / "backup.sqlite"
    assert main(["--output", str(out), "--template", str(skrooge_template)]) == 0
    import sqlite3

    conn = sqlite3.connect(str(out))
    assert conn.execute("SELECT COUNT(*) FROM operation").fetchone()[0] == 1


def test_export_cli_missing_token_exits_2(monkeypatch, tmp_path):
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    assert main(["--output", str(tmp_path / "b.qif")]) == 2


def test_export_cli_missing_template_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREFLY_TOKEN", "tok")
    monkeypatch.setattr(
        "skrooge2firefly.export_cli._connect_and_pull", lambda a, s: (_pulled_data(), {})
    )
    out = tmp_path / "backup.sqlite"
    assert main(["--output", str(out), "--template", str(tmp_path / "nope.sqlite")]) == 2
