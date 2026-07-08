import csv
import json
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.entities import Split, Transaction
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
