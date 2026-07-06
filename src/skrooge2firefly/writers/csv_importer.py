"""Writer that emits CSV + config JSON for the Firefly III Data Importer."""

from __future__ import annotations

import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.writers.base import WriteReport

logger = logging.getLogger(__name__)

_FIELDS = [
    "type",
    "date",
    "amount",
    "currency_code",
    "description",
    "source_name",
    "destination_name",
    "category",
    "tags",
    "foreign_amount",
    "foreign_currency_code",
    "external_id",
]

# Maps each CSV column index to a Data Importer "role". See
# https://docs.firefly-iii.org/how-to/data-importer/import/file/
_ROLES = [
    "transaction-type",
    "date_transaction",
    "amount",
    "currency-code",
    "description",
    "account-name",
    "opposing-name",
    "category-name",
    "tags-comma",
    "foreign-amount",
    "foreign-currency-code",
    "external-id",
]


def _signed(amount: Decimal, kind: str) -> str:
    return f"{-amount:.2f}" if kind == "withdrawal" else f"{amount:.2f}"


class CsvImporterWriter:
    """Emits ``transactions.csv`` and ``config.json`` into an output directory."""

    def __init__(self, *, output_dir: Path) -> None:
        """Create the writer.

        Args:
            output_dir: Directory to write ``transactions.csv`` and ``config.json``.

        """
        self._dir = output_dir

    def write(self, mapper: Mapper, *, only: set[str] | None = None) -> WriteReport:
        """Write the CSV and config; report budgets/recurrences as unsupported."""
        report = WriteReport()
        sections = only or {"accounts", "transactions", "budgets", "recurrences"}
        self._dir.mkdir(parents=True, exist_ok=True)
        if "transactions" in sections:
            self._write_csv(mapper, report)
            self._write_config()
        if "budgets" in sections and mapper.budgets:
            logger.warning(
                "Budgets are not supported by the Data Importer; skipping %d.",
                len(mapper.budgets),
            )
            for _ in mapper.budgets:
                report.skipped("budget")
        if "recurrences" in sections and mapper.recurrences:
            logger.warning(
                "Recurrences are not supported by the Data Importer; skipping %d.",
                len(mapper.recurrences),
            )
            for _ in mapper.recurrences:
                report.skipped("recurrence")
        return report

    def _write_csv(self, mapper: Mapper, report: WriteReport) -> None:
        path = self._dir / "transactions.csv"
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=_FIELDS)
            writer.writeheader()
            for txn in mapper.transactions:
                for split in txn.splits:
                    writer.writerow(
                        {
                            "type": txn.kind,
                            "date": txn.date,
                            "amount": _signed(split.amount, txn.kind),
                            "currency_code": split.currency_code,
                            "description": split.notes or txn.group_title or "(no description)",
                            "source_name": split.source_name,
                            "destination_name": split.destination_name,
                            "category": split.category_name or "",
                            "tags": ",".join(split.tags),
                            "foreign_amount": (
                                f"{split.foreign_amount:.2f}" if split.foreign_amount else ""
                            ),
                            "foreign_currency_code": split.foreign_currency_code or "",
                            "external_id": txn.external_id,
                        }
                    )
                report.created("transaction")

    def _write_config(self) -> None:
        config = {
            "version": 3,
            "source": "skrooge2firefly",
            "delimiter": "comma",
            "headers": True,
            "date_format": "Y-m-d",
            "default_account": 0,
            "duplicate_detection_method": "cell",
            "unique_column_index": _FIELDS.index("external_id"),
            "unique_column_type": "external-id",
            "roles": _ROLES,
            "do_mapping": [False] * len(_FIELDS),
            "mapping": [{} for _ in _FIELDS],
        }
        (self._dir / "config.json").write_text(json.dumps(config, indent=2))
