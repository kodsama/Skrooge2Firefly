import csv
import json
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.entities import Currency, Split, Transaction
from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.writers.csv_importer import CsvImporterWriter


def _mapper() -> Mapper:
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("12.50"), "SEK", "Checking", "ICA", category_name="Food")],
        ),
        Transaction(
            "skrooge:grp:7",
            "transfer",
            "2020-05-02",
            [Split(Decimal("500"), "SEK", "Checking", "Savings")],
        ),
    ]
    m.budgets = []
    return m


def test_writes_csv_and_config(tmp_path: Path):
    report = CsvImporterWriter(output_dir=tmp_path).write(_mapper())
    csv_path = tmp_path / "transactions.csv"
    config_path = tmp_path / "config.json"
    assert csv_path.exists() and config_path.exists()

    rows = list(csv.DictReader(csv_path.open()))
    assert rows[0]["type"] == "withdrawal"
    assert rows[0]["amount"] == "-12.50"  # withdrawals negative for the importer
    assert rows[0]["source_name"] == "Checking"
    assert rows[0]["destination_name"] == "ICA"
    assert rows[0]["external_id"] == "skrooge:op:1"
    assert rows[1]["type"] == "transfer"
    assert rows[1]["amount"] == "500.00"

    config = json.loads(config_path.read_text())
    assert config["roles"]  # non-empty role mapping
    assert report.counts["transaction"]["created"] == 2


def test_csv_dry_run_writes_no_files(tmp_path: Path):
    out = tmp_path / "out"
    report = CsvImporterWriter(output_dir=out, dry_run=True).write(_mapper())
    assert not out.exists()
    assert not (out / "transactions.csv").exists()
    assert not (out / "config.json").exists()
    assert report.counts["transaction"]["created"] == 2


def test_csv_non_dry_run_still_writes(tmp_path: Path):
    out = tmp_path / "out"
    report = CsvImporterWriter(output_dir=out, dry_run=False).write(_mapper())
    assert (out / "transactions.csv").exists()
    assert (out / "config.json").exists()
    assert report.counts["transaction"]["created"] == 2


def test_budgets_recurrences_reported_unsupported(tmp_path: Path):
    m = _mapper()
    from skrooge2firefly.model.entities import IRBudget

    m.budgets = [IRBudget(name="Food")]
    report = CsvImporterWriter(output_dir=tmp_path).write(m, only={"budgets", "transactions"})
    assert report.counts["budget"]["skipped"] == 1


def test_config_roles_are_valid(tmp_path: Path):
    CsvImporterWriter(output_dir=tmp_path).write(_mapper())
    config = json.loads((tmp_path / "config.json").read_text())
    roles = config["roles"]
    assert "_ignore" in roles
    assert "amount_foreign" in roles
    assert "transaction-type" not in roles
    assert "foreign-amount" not in roles


def test_amount_formatted_at_currency_precision(tmp_path: Path):
    m = Mapper()
    m.currencies = [
        Currency(code="BHD", name="Bahraini Dinar", symbol="BD", decimal_places=3, primary=False),
    ]
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "deposit",
            "2020-05-01",
            [Split(Decimal("12.345"), "BHD", "Cash Register", "Checking")],
        ),
        Transaction(
            "skrooge:op:2",
            "transfer",
            "2020-05-02",
            [Split(Decimal("500"), "SEK", "Checking", "Savings")],
        ),
    ]
    report = CsvImporterWriter(output_dir=tmp_path).write(m)
    rows = list(csv.DictReader((tmp_path / "transactions.csv").open()))
    assert rows[0]["amount"] == "12.345"
    assert rows[1]["amount"] == "500.00"
    assert report.counts["transaction"]["created"] == 2


def test_zero_amount_split_skipped(tmp_path: Path, caplog):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "deposit",
            "2020-05-01",
            [Split(Decimal("0.001"), "SEK", "Cash Register", "Checking")],
        ),
    ]
    with caplog.at_level("WARNING"):
        report = CsvImporterWriter(output_dir=tmp_path).write(m)
    rows = list(csv.DictReader((tmp_path / "transactions.csv").open()))
    assert rows == []
    assert report.counts["transaction"]["created"] == 0
    assert any("zero" in rec.message.lower() for rec in caplog.records)


def test_csv_written_utf8(tmp_path: Path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("10.00"), "SEK", "Checking", "Åhlens")],
        ),
    ]
    CsvImporterWriter(output_dir=tmp_path).write(m)
    rows = list(csv.DictReader((tmp_path / "transactions.csv").open(encoding="utf-8")))
    assert rows[0]["destination_name"] == "Åhlens"
