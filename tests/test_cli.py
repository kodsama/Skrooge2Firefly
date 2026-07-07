from pathlib import Path

import pytest

from skrooge2firefly.cli import _parse_only, build_parser, main
from skrooge2firefly.config import ConfigError


def test_parser_defaults():
    args = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com").parse_args(
        ["--target", "csv"]
    )
    assert args.target == "csv"
    assert args.input is None  # resolved later: --input > $SKROOGE_FILE > default
    assert args.url is None


def test_url_and_input_defaults_resolved_when_absent(monkeypatch):
    monkeypatch.delenv("FIREFLY_URL", raising=False)
    monkeypatch.delenv("SKROOGE_FILE", raising=False)
    from skrooge2firefly.config import Settings

    args = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com").parse_args(
        ["--target", "csv"]
    )
    settings = Settings.resolve(
        url=args.url,
        token="x",
        input_path=args.input,
        default_url="https://firefly.example.com",
        default_input="Skrooge-2026-05-30.sqlite",
    )
    assert settings.url == "https://firefly.example.com"
    assert settings.input == Path("Skrooge-2026-05-30.sqlite")


def test_csv_target_end_to_end(skrooge_db: Path, populate, tmp_path: Path):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": 0.0,
                "d_importdate": "2010-01-01",
            },
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "ICA"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": -10.0}])

    out = tmp_path / "out"
    code = main(
        ["--target", "csv", "--input", str(skrooge_db), "--output", str(out)],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 0
    assert (out / "transactions.csv").exists()


def test_api_target_without_token_errors(skrooge_db: Path, monkeypatch):
    monkeypatch.delenv("FIREFLY_TOKEN", raising=False)
    code = main(
        ["--target", "api", "--input", str(skrooge_db)],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 2


def test_api_connection_error_returns_2(skrooge_db, monkeypatch):
    import requests

    from skrooge2firefly.writers import client as client_mod

    monkeypatch.setenv("FIREFLY_TOKEN", "x")
    monkeypatch.setattr(
        client_mod.FireflyClient,
        "get_version",
        lambda self: (_ for _ in ()).throw(requests.ConnectionError("boom")),
    )
    code = main(
        ["--target", "api", "--input", str(skrooge_db)],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 2


def test_corrupt_sqlite_input_returns_2(tmp_path: Path):
    """A corrupt / non-SQLite input file must exit 2 without traceback (Fix A3)."""
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    code = main(
        ["--target", "csv", "--input", str(bad), "--output", str(tmp_path / "out")],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 2


def test_parser_accepts_assume_empty_target():
    args = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com").parse_args(
        ["--target", "api", "--assume-empty-target"]
    )
    assert args.assume_empty_target is True


def test_parser_accepts_concurrency():
    p = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com")
    args_with = p.parse_args(["--target", "api", "--concurrency", "8"])
    assert args_with.concurrency == 8
    args_default = p.parse_args(["--target", "api"])
    assert args_default.concurrency == 1


def test_parser_accepts_verify_flags():
    p = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com")
    args = p.parse_args(["--target", "api", "--verify", "--no-drill-down"])
    assert args.verify is True and args.no_drill_down is True


def test_verify_returns_1_on_mismatch(skrooge_db, populate, monkeypatch):
    populate(skrooge_db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(
        skrooge_db,
        "account",
        [
            {
                "id": 1,
                "t_name": "Checking",
                "t_type": "C",
                "f_importbalance": "",
                "d_importdate": "",
            },
        ],
    )
    populate(skrooge_db, "payee", [{"id": 5, "t_name": "Employer"}])
    populate(
        skrooge_db,
        "operation",
        [
            {"id": 1, "d_date": "2020-05-01", "rd_account_id": 1, "r_payee_id": 5, "rc_unit_id": 1},
        ],
    )
    populate(skrooge_db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": 100.0}])
    monkeypatch.setenv("FIREFLY_TOKEN", "x")

    from skrooge2firefly.writers import client as client_mod

    class StubClient:
        def __init__(self, *a, **k):
            pass

        def get_version(self):
            return "6.6.3"

        def account_balances(self, t):
            return {}

        def transaction_group_count(self):
            return 0

        def account_transactions(self, i):
            return []

    monkeypatch.setattr(client_mod, "FireflyClient", StubClient)
    code = main(
        ["--target", "api", "--verify", "--input", str(skrooge_db)],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 1


def test_parser_timeout_default_and_override():
    p = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com")
    assert p.parse_args(["--target", "api"]).timeout == 30.0
    assert p.parse_args(["--target", "api", "--timeout", "180"]).timeout == 180.0


def test_parser_accepts_update():
    p = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com")
    assert p.parse_args(["--target", "api", "--update"]).update is True
    assert p.parse_args(["--target", "api"]).update is False


def test_filter_transactions_by_date_window():
    from decimal import Decimal

    from skrooge2firefly.cli import filter_transactions
    from skrooge2firefly.model.entities import Split, Transaction

    def t(ext, date):
        return Transaction(ext, "withdrawal", date, [Split(Decimal("1"), "SEK", "A", "B")])

    txns = [t("a", "2026-04-15"), t("b", "2026-05-10"), t("c", "2026-05-31"), t("d", "2026-06-02")]
    kept = filter_transactions(txns, "2026-05-01", "2026-05-31")
    assert [x.external_id for x in kept] == ["b", "c"]
    assert len(filter_transactions(txns, "2026-05-01", None)) == 3  # b, c, d
    assert len(filter_transactions(txns, None, None)) == 4


def test_parser_accepts_since_until():
    p = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com")
    a = p.parse_args(["--target", "api", "--since", "2026-05-01", "--until", "2026-05-31"])
    assert a.since == "2026-05-01" and a.until == "2026-05-31"


# --- automatic post-import verification ---


def _stub_writer_and_verify(monkeypatch, verify_rc: int = 0):
    from skrooge2firefly.writers.base import WriteReport

    calls: dict[str, int] = {"verify": 0}
    monkeypatch.setenv("FIREFLY_TOKEN", "tok")
    monkeypatch.setattr("skrooge2firefly.cli._run_writer", lambda *a, **k: WriteReport())

    def fake_verify(args, settings, mapper):
        calls["verify"] += 1
        return verify_rc

    monkeypatch.setattr("skrooge2firefly.cli._run_verify", fake_verify)
    return calls


def test_api_import_runs_verification_at_end(monkeypatch, skrooge_db: Path):
    calls = _stub_writer_and_verify(monkeypatch)
    rc = main(["--target", "api", "--input", str(skrooge_db)])
    assert rc == 0
    assert calls["verify"] == 1


def test_auto_verify_mismatch_sets_exit_code(monkeypatch, skrooge_db: Path):
    calls = _stub_writer_and_verify(monkeypatch, verify_rc=1)
    rc = main(["--target", "api", "--input", str(skrooge_db)])
    assert rc == 1
    assert calls["verify"] == 1


def test_skip_verify_flag_disables_auto_verify(monkeypatch, skrooge_db: Path):
    calls = _stub_writer_and_verify(monkeypatch)
    rc = main(["--target", "api", "--input", str(skrooge_db), "--skip-verify"])
    assert rc == 0
    assert calls["verify"] == 0


def test_partial_import_skips_auto_verify(monkeypatch, skrooge_db: Path):
    calls = _stub_writer_and_verify(monkeypatch)
    rc = main(["--target", "api", "--input", str(skrooge_db), "--since", "2020-01-01"])
    assert rc == 0
    assert calls["verify"] == 0


def test_dry_run_skips_auto_verify(monkeypatch, skrooge_db: Path):
    calls = _stub_writer_and_verify(monkeypatch)
    rc = main(["--target", "api", "--input", str(skrooge_db), "--dry-run"])
    assert rc == 0
    assert calls["verify"] == 0


def test_csv_target_skips_auto_verify(monkeypatch, skrooge_db: Path, tmp_path: Path):
    calls = _stub_writer_and_verify(monkeypatch)
    rc = main(["--target", "csv", "--input", str(skrooge_db), "--output", str(tmp_path / "o")])
    assert rc == 0
    assert calls["verify"] == 0


def _base_ledger_seed(populate, db):
    populate(db, "unit", [{"id": 1, "t_name": "SEK", "t_symbol": "SEK", "t_type": "1"}])
    populate(db, "account", [{"id": 1, "t_name": "Checking", "t_type": "C"}])
    populate(
        db, "operation", [{"id": 1, "d_date": "2020-01-01", "rd_account_id": 1, "rc_unit_id": 1}]
    )
    populate(db, "suboperation", [{"id": 1, "rd_operation_id": 1, "f_value": -10.0}])


def test_dry_run_verifies_connectivity(skrooge_db, populate, monkeypatch):
    """--dry-run must still connect + authenticate (a pre-flight check): a
    failing get_version aborts with exit 2 before any mapping is trusted."""
    _base_ledger_seed(populate, skrooge_db)
    monkeypatch.setenv("FIREFLY_TOKEN", "x")
    from skrooge2firefly.writers import client as client_mod
    from skrooge2firefly.writers.client import FireflyError

    monkeypatch.setattr(
        client_mod.FireflyClient,
        "get_version",
        lambda self: (_ for _ in ()).throw(FireflyError("Authentication failed (401)")),
    )
    code = main(
        ["--target", "api", "--input", str(skrooge_db), "--dry-run"],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 2


def test_parse_only_accepts_recurrences():
    assert _parse_only("recurrences") == {"recurrences"}


def test_parse_only_rejects_subscriptions():
    # the old section name is no longer valid after the rename to "recurrences"
    with pytest.raises(ConfigError):
        _parse_only("subscriptions")
