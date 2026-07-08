from pathlib import Path

import pytest

from skrooge2firefly.cli import _parse_only, _validate_date, build_parser, main
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


def test_csv_dry_run_writes_no_files(skrooge_db: Path, populate, tmp_path: Path):
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
        ["--target", "csv", "--dry-run", "--input", str(skrooge_db), "--output", str(out)],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 0
    assert not out.exists()


def test_csv_target_warns_ignored_flags(skrooge_db: Path, tmp_path: Path, caplog):
    out = tmp_path / "out"
    with caplog.at_level("WARNING", logger="skrooge2firefly"):
        code = main(
            [
                "--target",
                "csv",
                "--input",
                str(skrooge_db),
                "--output",
                str(out),
                "--update",
                "--strict",
                "--assume-empty-target",
                "--concurrency",
                "3",
            ],
            default_input="Skrooge-2026-05-30.sqlite",
            default_url="https://firefly.example.com",
        )
    assert code == 0
    text = "\n".join(caplog.messages)
    assert "--update" in text
    assert "--strict" in text
    assert "--assume-empty-target" in text
    assert "--concurrency" in text


def test_csv_target_default_concurrency_is_one():
    args = build_parser("Skrooge-2026-05-30.sqlite", "https://firefly.example.com").parse_args(
        ["--target", "csv"]
    )
    assert args.concurrency == 1


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


def test_since_rejects_unpadded_date(skrooge_db: Path, monkeypatch):
    with pytest.raises(ConfigError):
        _validate_date("2020-1-5", "--since")

    monkeypatch.setenv("FIREFLY_TOKEN", "x")
    code = main(
        ["--target", "csv", "--input", str(skrooge_db), "--since", "2020-1-5"],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 2


def test_valid_dates_accepted():
    _validate_date("2020-01-05", "--since")
    _validate_date(None, "--since")


def test_calendar_invalid_date_rejected():
    with pytest.raises(ConfigError):
        _validate_date("2020-02-30", "--until")


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


# --- orphan flags / decisions file / dry-run plan ---


def test_parser_accepts_orphans_and_decisions():
    args = build_parser("x.sqlite", "https://f").parse_args(
        ["--target", "api", "--update", "--orphans", "delete", "--decisions", "d.json"]
    )
    assert args.orphans == "delete"
    assert str(args.decisions) == "d.json"


def test_orphans_rejects_bad_value():
    with pytest.raises(SystemExit):
        build_parser("x.sqlite", "https://f").parse_args(["--target", "api", "--orphans", "wipe"])


def test_orphans_and_decisions_default_to_none():
    args = build_parser("x.sqlite", "https://f").parse_args(["--target", "api"])
    assert args.orphans is None
    assert args.decisions is None


def test_build_decider_delete_flag_returns_fixed_delete():
    from skrooge2firefly.cli import build_decider
    from skrooge2firefly.writers.orphans import FixedDecider

    args = build_parser("x.sqlite", "https://f").parse_args(
        ["--target", "api", "--orphans", "delete"]
    )
    decider = build_decider(args)
    assert isinstance(decider, FixedDecider)
    assert decider.decide("transaction", "k", "label") == "delete"


def test_build_decider_report_flag_returns_fixed_ignore():
    from skrooge2firefly.cli import build_decider
    from skrooge2firefly.writers.orphans import FixedDecider

    args = build_parser("x.sqlite", "https://f").parse_args(
        ["--target", "api", "--orphans", "report"]
    )
    decider = build_decider(args)
    assert isinstance(decider, FixedDecider)
    assert decider.decide("transaction", "k", "label") == "ignore"


def test_build_decider_non_tty_without_flag_defaults_to_ignore(monkeypatch):
    from skrooge2firefly.cli import build_decider

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = build_parser("x.sqlite", "https://f").parse_args(["--target", "api"])
    decider = build_decider(args)
    assert decider.decide("transaction", "k", "label") == "ignore"


def test_build_decider_interactive_without_flag_prompts(monkeypatch):
    from skrooge2firefly.cli import build_decider
    from skrooge2firefly.writers.orphans import PromptDecider

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    args = build_parser("x.sqlite", "https://f").parse_args(["--target", "api"])
    decider = build_decider(args)
    assert isinstance(decider, PromptDecider)


def test_run_writer_passes_orphan_decider_and_decisions_path(skrooge_db, monkeypatch):
    monkeypatch.setenv("FIREFLY_TOKEN", "x")

    from skrooge2firefly.writers import client as client_mod
    from skrooge2firefly.writers import firefly_api as firefly_api_mod
    from skrooge2firefly.writers.base import WriteReport
    from skrooge2firefly.writers.orphans import FixedDecider

    captured: dict = {}

    class StubClient:
        def __init__(self, *a, **k):
            pass

        def get_version(self):
            return "6.6.3"

    class StubWriter:
        def __init__(self, client, **kwargs):
            captured.update(kwargs)

        def write(self, mapper, only=None):
            return WriteReport()

    monkeypatch.setattr(client_mod, "FireflyClient", StubClient)
    monkeypatch.setattr(firefly_api_mod, "FireflyApiWriter", StubWriter)
    monkeypatch.setattr("skrooge2firefly.cli._run_verify", lambda *a, **k: 0)

    code = main(
        [
            "--target",
            "api",
            "--input",
            str(skrooge_db),
            "--update",
            "--orphans",
            "delete",
            "--decisions",
            "d.json",
        ],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 0
    assert isinstance(captured["orphan_decider"], FixedDecider)
    assert captured["orphan_decider"].decide("transaction", "k", "label") == "delete"
    assert str(captured["decisions_path"]) == "d.json"
    assert captured["resolve_orphans"] is True


def test_run_writer_disables_orphan_resolution_when_since_is_set(skrooge_db, monkeypatch):
    monkeypatch.setenv("FIREFLY_TOKEN", "x")

    from skrooge2firefly.writers import client as client_mod
    from skrooge2firefly.writers import firefly_api as firefly_api_mod
    from skrooge2firefly.writers.base import WriteReport

    captured: dict = {}

    class StubClient:
        def __init__(self, *a, **k):
            pass

        def get_version(self):
            return "6.6.3"

    class StubWriter:
        def __init__(self, client, **kwargs):
            captured.update(kwargs)

        def write(self, mapper, only=None):
            return WriteReport()

    monkeypatch.setattr(client_mod, "FireflyClient", StubClient)
    monkeypatch.setattr(firefly_api_mod, "FireflyApiWriter", StubWriter)
    monkeypatch.setattr("skrooge2firefly.cli._run_verify", lambda *a, **k: 0)

    code = main(
        [
            "--target",
            "api",
            "--input",
            str(skrooge_db),
            "--update",
            "--since",
            "2024-01-01",
            "--orphans",
            "delete",
        ],
        default_input="Skrooge-2026-05-30.sqlite",
        default_url="https://firefly.example.com",
    )
    assert code == 0
    assert captured["resolve_orphans"] is False


def test_dry_run_update_reports_per_type_plan(caplog):
    import logging

    from skrooge2firefly.cli import _report_preflight
    from skrooge2firefly.writers.base import WriteReport

    report = WriteReport()
    report.created("transaction")
    report.updated("transaction")
    report.skipped("transaction")
    report.deleted("transaction")

    with caplog.at_level(logging.INFO, logger="skrooge2firefly"):
        code = _report_preflight(report, update=True)

    assert code == 0
    text = "\n".join(caplog.messages)
    assert "transaction" in text
    assert "create=1" in text
    assert "update=1" in text
    assert "delete=1" in text
