"""Command-line entry point and orchestration."""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import requests

from skrooge2firefly.config import ConfigError, Settings
from skrooge2firefly.model.entities import Transaction
from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.skrooge.reader import SkroogeReader
from skrooge2firefly.writers.base import WriteReport
from skrooge2firefly.writers.client import FireflyError
from skrooge2firefly.writers.csv_importer import CsvImporterWriter

logger = logging.getLogger("skrooge2firefly")

_DEFAULT_INPUT = "Skrooge.sqlite"
_DEFAULT_URL = "https://firefly.example.com"
_SECTIONS = ["accounts", "transactions", "budgets", "subscriptions"]


_EPILOG = """\
configuration (.env in the working directory, real environment wins over .env):
  SKROOGE_FILE   source Skrooge .sqlite file   (CLI --input overrides)
  FIREFLY_URL    destination Firefly-III URL   (CLI --url overrides)
  FIREFLY_TOKEN  Firefly personal access token (CLI --token overrides)

examples:
  skrooge2firefly --target api --dry-run          # pre-flight: validate, no writes
  skrooge2firefly --target api                    # full import + auto-verify (resumable)
  skrooge2firefly --target api --update           # re-sync existing records
  skrooge2firefly --target api --verify           # read-only parity check only
  skrooge2firefly --target csv --output ./out     # CSVs for the Data Importer

Every full API import ends with an automatic verification pass comparing
per-account balances and transaction counts against the Skrooge source
(disable with --skip-verify).

exit codes:
  0  success and verification matches
  1  some records failed, or verification found a mismatch
  2  configuration, input-file, or Firefly API error
"""


def build_parser(default_input: str, default_url: str) -> argparse.ArgumentParser:
    """Build the argument parser."""
    p = argparse.ArgumentParser(
        prog="skrooge2firefly",
        description="Import a Skrooge SQLite export into Firefly-III.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--target", choices=["api", "csv"], required=True, help="Write target.")
    p.add_argument(
        "--input",
        type=Path,
        default=None,
        help=f"Skrooge SQLite file (default: $SKROOGE_FILE, else {default_input}).",
    )
    p.add_argument(
        "--url", default=None, help=f"Firefly URL (default: $FIREFLY_URL, else {default_url})."
    )
    p.add_argument("--token", default=None, help="Firefly token (default: $FIREFLY_TOKEN).")
    p.add_argument("--output", type=Path, default=Path("out"), help="CSV output directory.")
    p.add_argument(
        "--ledger", type=Path, default=Path(".import-state.json"), help="Idempotency ledger path."
    )
    p.add_argument(
        "--only",
        default=None,
        help=f"Comma-separated subset of sections: {','.join(_SECTIONS)}.",
    )
    p.add_argument(
        "--since", default=None, help="Only import transactions on/after this date (YYYY-MM-DD)."
    )
    p.add_argument(
        "--until", default=None, help="Only import transactions on/before this date (YYYY-MM-DD)."
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Pre-flight only: connect, map, and validate everything that would "
        "be written (no changes). Exits 1 if any record would be rejected.",
    )
    p.add_argument(
        "--update",
        action="store_true",
        help="Upsert: re-sync existing transactions and mirror account open/closed state.",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="Read-only: compare Skrooge against Firefly and report parity (no writes).",
    )
    p.add_argument(
        "--no-drill-down",
        action="store_true",
        help="With --verify, skip the per-month delta drill-down for mismatched accounts.",
    )
    p.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip the automatic post-import verification run after a full API import.",
    )
    p.add_argument("--strict", action="store_true", help="Abort on the first row error.")
    p.add_argument(
        "--assume-empty-target",
        action="store_true",
        help="Skip reconciliation reads (use only when the Firefly instance is known to be empty).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel transaction POSTs for the API target (default 1).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request HTTP timeout in seconds (raise for slow/overloaded servers).",
    )
    p.add_argument("--log-level", default="INFO", help="Logging level (default: INFO).")
    p.add_argument("-v", "--verbose", action="store_true", help="Shortcut for --log-level DEBUG.")
    return p


def _parse_only(value: str | None) -> set[str] | None:
    if not value:
        return None
    requested = {s.strip() for s in value.split(",") if s.strip()}
    invalid = requested - set(_SECTIONS)
    if invalid:
        raise ConfigError(f"Unknown --only sections: {', '.join(sorted(invalid))}")
    return requested


def filter_transactions(
    transactions: list[Transaction], since: str | None, until: str | None
) -> list[Transaction]:
    """Return transactions whose date is within [since, until] (inclusive).

    Dates are ``YYYY-MM-DD`` strings, which compare correctly lexicographically.
    A None bound is open-ended.
    """
    return [
        t
        for t in transactions
        if (since is None or t.date >= since) and (until is None or t.date <= until)
    ]


def main(
    argv: list[str] | None = None,
    *,
    default_input: str = _DEFAULT_INPUT,
    default_url: str = _DEFAULT_URL,
) -> int:
    """Run the CLI. Returns a process exit code."""
    args = build_parser(default_input, default_url).parse_args(argv)
    logging.basicConfig(
        level="DEBUG" if args.verbose else args.log_level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        only = _parse_only(args.only)
        settings = Settings.resolve(
            url=args.url,
            token=args.token,
            input_path=args.input,
            default_url=default_url,
            default_input=default_input,
        )

        logger.info("Reading Skrooge file %s", settings.input)
        with SkroogeReader(settings.input) as reader:
            mapper = Mapper.build(reader)
        logger.info(
            "Mapped %d accounts, %d transactions, %d budgets, %d recurrences (%d warnings)",
            len(mapper.accounts),
            len(mapper.transactions),
            len(mapper.budgets),
            len(mapper.recurrences),
            len(mapper.warnings),
        )
        for w in mapper.warnings:
            logger.debug("warning: %s", w)

        if args.since or args.until:
            before = len(mapper.transactions)
            mapper.transactions = filter_transactions(mapper.transactions, args.since, args.until)
            logger.info(
                "Date filter [%s..%s]: %d of %d transactions kept",
                args.since or "-",
                args.until or "-",
                len(mapper.transactions),
                before,
            )

        if args.verify:
            return _run_verify(args, settings, mapper)

        report = _run_writer(args, settings, mapper, only)
        logger.info("Done.\n%s", report.summary())
        if args.dry_run and args.target == "api":
            return _report_preflight(report)
        if report.has_failures and not args.dry_run:
            for kind, msg in report.errors[:20]:
                logger.error("%s failed: %s", kind, msg)
            return 1
        if _should_auto_verify(args, only):
            logger.info("Post-import verification (disable with --skip-verify)...")
            return _run_verify(args, settings, mapper)
        return 0
    except sqlite3.Error as exc:
        logger.error("Cannot read Skrooge file: %s", exc)
        return 2
    except (FireflyError, requests.RequestException) as exc:
        logger.error("Firefly API error: %s", exc)
        return 2
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2


def _report_preflight(report: WriteReport) -> int:
    """Summarise an API --dry-run as a pre-flight verdict; return the exit code.

    In dry-run the writer performs no writes: ``created`` counts what *would*
    be created, ``skipped`` what already exists on the instance, and ``failed``
    records that would be rejected (e.g. an amount that rounds to 0.00).
    """
    would_create = sum(c["created"] for c in report.counts.values())
    already = sum(c["skipped"] for c in report.counts.values())
    issues = sum(c["failed"] for c in report.counts.values())
    logger.info(
        "Pre-flight: %d record(s) would be created, %d already present, %d issue(s). "
        "Nothing was written.",
        would_create,
        already,
        issues,
    )
    if issues:
        for kind, msg in report.errors[:20]:
            logger.error("pre-flight issue — %s: %s", kind, msg)
        logger.error("Pre-flight FAILED — fix the above before importing.")
        return 1
    logger.info("Pre-flight OK — re-run without --dry-run to import.")
    return 0


def _should_auto_verify(args: argparse.Namespace, only: set[str] | None) -> bool:
    """Return True when a run should end with an automatic parity check.

    Only full API imports are verified: partial runs (``--only``, ``--since``,
    ``--until``), dry runs, and the CSV target would always report mismatches.
    """
    return (
        args.target == "api"
        and not args.dry_run
        and not args.skip_verify
        and only is None
        and args.since is None
        and args.until is None
    )


def _run_writer(
    args: argparse.Namespace,
    settings: Settings,
    mapper: Mapper,
    only: set[str] | None,
) -> WriteReport:
    if args.target == "csv":
        return CsvImporterWriter(output_dir=args.output).write(mapper, only=only)

    # API target — imported lazily so CSV runs need no network deps configured.
    from skrooge2firefly.writers.client import FireflyClient
    from skrooge2firefly.writers.firefly_api import FireflyApiWriter

    token = settings.require_token()
    client = FireflyClient(settings.url, token, timeout=args.timeout)
    # Always connect — get_version verifies URL + token, which is itself a
    # pre-flight check under --dry-run.
    version = client.get_version()
    logger.info(
        "%s Firefly %s", "Pre-flight — connected to" if args.dry_run else "Connected to", version
    )
    writer = FireflyApiWriter(
        client,
        ledger_path=args.ledger,
        dry_run=args.dry_run,
        strict=args.strict,
        assume_empty=args.assume_empty_target,
        concurrency=args.concurrency,
        update=args.update,
    )
    return writer.write(mapper, only=only)


def _run_verify(args: argparse.Namespace, settings: Settings, mapper: Mapper) -> int:
    """Run read-only verification; return 0 if everything matches, else 1."""
    from skrooge2firefly.verify import verify
    from skrooge2firefly.writers.client import FireflyClient

    client = FireflyClient(settings.url, settings.require_token(), timeout=args.timeout)
    logger.info("Connected to Firefly %s", client.get_version())
    report = verify(mapper, client, drill_down=not args.no_drill_down)

    matched = sum(1 for c in report.comparisons if c.balance_ok)
    logger.info(
        "Verify: %d/%d accounts match | journals expected=%d actual=%d | %d Skrooge ops excluded",
        matched,
        len(report.comparisons),
        report.expected_group_count,
        report.actual_group_count,
        report.excluded_ops,
    )
    for c in report.comparisons:
        if c.balance_ok:
            continue
        if c.mixed_currency:
            logger.info(
                "SKIP (mixed-currency) %-24s %s expected=%s actual=%s — Firefly values "
                "foreign-currency operations by its own exchange rates",
                c.name,
                c.currency,
                c.expected_balance,
                c.actual_balance,
            )
            continue
        actual = "absent" if not c.present else f"{c.actual_balance}"
        logger.warning(
            "MISMATCH %-34s %s expected=%s actual=%s",
            c.name,
            c.currency,
            c.expected_balance,
            actual,
        )
        for period, ev, av in c.month_diffs:
            logger.warning("    %s expected=%s actual=%s", period, ev, av)

    # Mandatory independent latest-balance check: Skrooge's own balance vs
    # Firefly's, straight from the Skrooge file (not via the import mapping).
    parities = report.balance_parities
    balance_matched = sum(1 for b in parities if b.ok)
    informational = [b for b in parities if not b.ok and b.informational]
    hard_fail = [b for b in parities if not b.ok and not b.informational]
    logger.info(
        "Latest-balance check (Skrooge vs Firefly): %d/%d accounts match "
        "(%d informational, %d failing)",
        balance_matched,
        len(parities),
        len(informational),
        len(hard_fail),
    )
    for b in hard_fail:
        actual = "absent" if b.firefly_balance is None else f"{b.firefly_balance}"
        logger.warning(
            "BALANCE MISMATCH %-30s skrooge=%s firefly=%s", b.name, b.skrooge_balance, actual
        )
    for b in informational:
        logger.info(
            "  balance differs (expected) %-28s skrooge=%s firefly=%s — %s",
            b.name,
            b.skrooge_balance,
            b.firefly_balance,
            b.reason,
        )
    return 0 if report.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
