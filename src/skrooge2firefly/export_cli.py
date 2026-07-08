"""Command-line entry point for exporting Firefly-III back to Skrooge."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import requests

from skrooge2firefly.config import ConfigError, Settings
from skrooge2firefly.export.puller import PulledData, pull
from skrooge2firefly.export.qif import ledger_entries, render_qif
from skrooge2firefly.export.skg import SkgExportError, write_skg
from skrooge2firefly.export.verify import AccountParity, verify_qif, verify_skg
from skrooge2firefly.writers.client import FireflyError

logger = logging.getLogger("firefly2skrooge")

_DEFAULT_URL = "https://firefly.example.com"
_DEFAULT_TEMPLATE = "Skrooge.sqlite"

_EPILOG = """\
configuration (.env in the working directory, real environment wins over .env):
  FIREFLY_URL      source Firefly-III URL          (CLI --url overrides)
  FIREFLY_TOKEN    Firefly personal access token   (CLI --token overrides)
  SKROOGE_TEMPLATE template .sqlite for --format sqlite
                   (falls back to $SKROOGE_FILE, then the repo default)

examples:
  firefly2skrooge --output backup.qif              # QIF (Skrooge's importer reads it)
  firefly2skrooge --output backup.sqlite           # native Skrooge sqlite (template copy)

exit codes:
  0  file written and self-verification matches
  1  self-verification found a mismatch
  2  configuration, template, or Firefly API error
"""


def build_parser(default_url: str) -> argparse.ArgumentParser:
    """Build the argument parser."""
    p = argparse.ArgumentParser(
        prog="firefly2skrooge",
        description="Export a Firefly-III instance to a Skrooge-importable file.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("firefly-export.qif"),
        help="Output file (default: firefly-export.qif).",
    )
    p.add_argument(
        "--format",
        choices=["qif", "sqlite"],
        default=None,
        help="Output format (default: inferred from --output extension).",
    )
    p.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Skrooge sqlite template for --format sqlite "
        "(default: $SKROOGE_TEMPLATE, else $SKROOGE_FILE).",
    )
    p.add_argument(
        "--url", default=None, help=f"Firefly URL (default: $FIREFLY_URL, else {default_url})."
    )
    p.add_argument("--token", default=None, help="Firefly token (default: $FIREFLY_TOKEN).")
    p.add_argument(
        "--timeout", type=float, default=30.0, help="Per-request HTTP timeout in seconds."
    )
    p.add_argument("--log-level", default="INFO", help="Logging level (default: INFO).")
    p.add_argument("-v", "--verbose", action="store_true", help="Shortcut for --log-level DEBUG.")
    return p


def infer_format(output: Path, explicit: str | None) -> str:
    """Return the output format, inferring sqlite from .sqlite/.skg extensions."""
    if explicit:
        return explicit
    return "sqlite" if output.suffix.lower() in (".sqlite", ".skg") else "qif"


def _connect_and_pull(
    args: argparse.Namespace, settings: Settings
) -> tuple[PulledData, dict[str, int]]:
    from skrooge2firefly.writers.client import FireflyClient

    client = FireflyClient(settings.url, settings.require_token(), timeout=args.timeout)
    logger.info("Connected to Firefly %s", client.get_version())
    return pull(client), client.currency_decimals()


def _report(parities: list[AccountParity]) -> bool:
    ok = True
    for p in parities:
        if p.ok:
            continue
        ok = False
        logger.warning(
            "MISMATCH %-30s entries expected=%d actual=%d sum expected=%s actual=%s",
            p.name,
            p.expected_count,
            p.actual_count,
            p.expected_sum,
            p.actual_sum,
        )
    return ok


def main(argv: list[str] | None = None) -> int:
    """Run the exporter CLI. Returns a process exit code."""
    args = build_parser(_DEFAULT_URL).parse_args(argv)
    logging.basicConfig(
        level="DEBUG" if args.verbose else args.log_level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.resolve(
            url=args.url,
            token=args.token,
            input_path=None,
            default_url=_DEFAULT_URL,
            default_input=_DEFAULT_TEMPLATE,
        )
        fmt = infer_format(args.output, args.format)
        data, decimals = _connect_and_pull(args, settings)
        logger.info(
            "Pulled %d accounts, %d transactions, %d budgets",
            len(data.accounts),
            len(data.transactions),
            len(data.budgets),
        )
        if fmt == "qif":
            text = render_qif(
                data.accounts, ledger_entries(data.accounts, data.transactions), decimals
            )
            args.output.write_text(text, encoding="utf-8")
            if data.budgets:
                logger.warning("QIF cannot represent budgets; %d skipped", len(data.budgets))
            parities = verify_qif(text, data.accounts, data.transactions, decimals)
        else:
            template = args.template or Path(os.environ.get("SKROOGE_TEMPLATE") or settings.input)
            report = write_skg(template, args.output, data)
            for w in report.warnings:
                logger.warning("%s", w)
            logger.info("Wrote %d operations, %d budget rows", report.operations, report.budgets)
            parities = verify_skg(args.output, data.accounts, data.transactions, decimals)
        if not _report(parities):
            logger.error("Self-verification FAILED for %s", args.output)
            return 1
        logger.info("Wrote and verified %s (%s)", args.output, fmt)
        return 0
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except SkgExportError as exc:
        logger.error("%s", exc)
        return 2
    except (FireflyError, requests.RequestException) as exc:
        logger.error("Firefly API error: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
