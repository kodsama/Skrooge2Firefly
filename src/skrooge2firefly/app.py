"""Unified `skrooge-firefly` command: one of --import FILE or --export FILE.

A thin front end that parses the direction-neutral interface and delegates to
the import or export orchestration. Exactly one of --import / --export is
required; passing both is rejected as ambiguous.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from skrooge2firefly.cli import main as import_main
from skrooge2firefly.export_cli import main as export_main

_EPILOG = """\
configuration (.env in the working directory; real environment wins over .env):
  SKROOGE_FILE      default Skrooge .sqlite for --import
  FIREFLY_URL       Firefly III base URL          (--url overrides)
  FIREFLY_TOKEN     Firefly personal access token (--token overrides)
  SKROOGE_TEMPLATE  template .sqlite for --export to a native Skrooge file

examples:
  skrooge-firefly --import Skrooge.sqlite               # import into Firefly (+ auto-verify)
  skrooge-firefly --import Skrooge.sqlite --dry-run     # pre-flight only, no writes
  skrooge-firefly --import Skrooge.sqlite --verify      # read-only parity check
  skrooge-firefly --export backup.qif                  # export Firefly -> QIF
  skrooge-firefly --export backup.sqlite               # export Firefly -> native Skrooge

exit codes:
  0  success (import + verification matched, or export verified, or dry-run OK)
  1  records failed, verification mismatch, or a dry-run issue
  2  configuration, input-file, or Firefly API error
"""

# Import-only and export-only flags, used to reject cross-mode misuse.
_IMPORT_ONLY = (
    "target_set",
    "csv_out",
    "ledger",
    "only",
    "since",
    "until",
    "dry_run",
    "update",
    "orphans",
    "decisions",
    "verify",
    "no_drill_down",
    "skip_verify",
    "strict",
    "assume_empty_target",
    "concurrency",
)
_EXPORT_ONLY = ("format", "template")


def build_parser() -> argparse.ArgumentParser:
    """Build the unified argument parser."""
    p = argparse.ArgumentParser(
        prog="skrooge-firefly",
        description="Migrate between Skrooge and Firefly III. "
        "Choose a direction with --import or --export.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--import",
        dest="import_file",
        metavar="FILE",
        type=Path,
        help="Import this Skrooge .sqlite file into Firefly III.",
    )
    mode.add_argument(
        "--export",
        dest="export_file",
        metavar="FILE",
        type=Path,
        help="Export Firefly III to this file (.qif, or .sqlite/.skg for a native Skrooge file).",
    )

    conn = p.add_argument_group("connection (both directions)")
    conn.add_argument("--url", default=None, help="Firefly URL (default: $FIREFLY_URL).")
    conn.add_argument("--token", default=None, help="Firefly token (default: $FIREFLY_TOKEN).")
    conn.add_argument("--timeout", default=None, help="Per-request HTTP timeout in seconds.")
    conn.add_argument("--log-level", default=None, help="Logging level (default: INFO).")
    conn.add_argument(
        "-v", "--verbose", action="store_true", help="Shortcut for --log-level DEBUG."
    )

    imp = p.add_argument_group("import options (with --import)")
    imp.add_argument(
        "--target",
        choices=["api", "csv"],
        default="api",
        help="Write directly to the API (default) or emit CSV for the Data Importer.",
    )
    imp.add_argument(
        "--csv-out", default=None, help="Output directory for --target csv (default: out)."
    )
    imp.add_argument(
        "--ledger", default=None, help="Idempotency ledger path (default: .import-state.json)."
    )
    imp.add_argument("--only", default=None, help="Comma-separated sections to import.")
    imp.add_argument("--since", default=None, help="Only import transactions on/after YYYY-MM-DD.")
    imp.add_argument("--until", default=None, help="Only import transactions on/before YYYY-MM-DD.")
    imp.add_argument(
        "--dry-run", action="store_true", help="Pre-flight only: validate, write nothing."
    )
    imp.add_argument(
        "--update", action="store_true", help="Re-sync existing records instead of skipping."
    )
    imp.add_argument(
        "--orphans",
        choices=["report", "delete", "ignore"],
        default=None,
        help="With --update: what to do with records in Firefly but absent from the "
        "file. Default: prompt when interactive, else report.",
    )
    imp.add_argument(
        "--decisions",
        default=None,
        help="Orphan-decisions plan file: written in --dry-run, applied on the real run.",
    )
    imp.add_argument(
        "--verify", action="store_true", help="Read-only parity check against the file."
    )
    imp.add_argument(
        "--no-drill-down", action="store_true", help="With --verify, skip per-month deltas."
    )
    imp.add_argument(
        "--skip-verify", action="store_true", help="Skip the automatic post-import verify."
    )
    imp.add_argument("--strict", action="store_true", help="Abort on the first row error.")
    imp.add_argument(
        "--assume-empty-target",
        action="store_true",
        help="Skip reconciliation reads (empty instance).",
    )
    imp.add_argument("--concurrency", default=None, help="Parallel transaction POSTs (default 4).")

    exp = p.add_argument_group("export options (with --export)")
    exp.add_argument(
        "--format",
        choices=["qif", "sqlite"],
        default=None,
        help="Override the inferred export format.",
    )
    exp.add_argument(
        "--template", default=None, help="Skrooge .sqlite template for --format sqlite."
    )
    return p


def _shared_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.url is not None:
        argv += ["--url", args.url]
    if args.token is not None:
        argv += ["--token", args.token]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.log_level is not None:
        argv += ["--log-level", args.log_level]
    if args.verbose:
        argv += ["-v"]
    return argv


def _import_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--target", args.target, "--input", str(args.import_file)]
    argv += _shared_argv(args)
    if args.csv_out is not None:
        argv += ["--output", args.csv_out]
    if args.ledger is not None:
        argv += ["--ledger", args.ledger]
    if args.only is not None:
        argv += ["--only", args.only]
    if args.since is not None:
        argv += ["--since", args.since]
    if args.until is not None:
        argv += ["--until", args.until]
    if args.concurrency is not None:
        argv += ["--concurrency", str(args.concurrency)]
    if args.orphans is not None:
        argv += ["--orphans", args.orphans]
    if args.decisions is not None:
        argv += ["--decisions", args.decisions]
    for flag in (
        "dry_run",
        "update",
        "verify",
        "no_drill_down",
        "skip_verify",
        "strict",
        "assume_empty_target",
    ):
        if getattr(args, flag):
            argv += ["--" + flag.replace("_", "-")]
    return argv


def _export_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--output", str(args.export_file)]
    argv += _shared_argv(args)
    if args.format is not None:
        argv += ["--format", args.format]
    if args.template is not None:
        argv += ["--template", args.template]
    return argv


def _misused(args: argparse.Namespace, names: tuple[str, ...]) -> list[str]:
    """Return the given flags that were set to a non-default value."""
    used = []
    for name in names:
        if name == "target_set":
            if args.target != "api":
                used.append("--target")
        elif name in (
            "dry_run",
            "update",
            "verify",
            "no_drill_down",
            "skip_verify",
            "strict",
            "assume_empty_target",
        ):
            if getattr(args, name):
                used.append("--" + name.replace("_", "-"))
        elif getattr(args, name) is not None:
            used.append("--" + name.replace("_", "-"))
    return used


def main(argv: list[str] | None = None) -> int:
    """Parse the unified interface and delegate to import or export."""
    args = build_parser().parse_args(argv)
    if args.import_file is not None:
        misused = _misused(args, _EXPORT_ONLY)
        if misused:
            print(f"error: {', '.join(misused)} apply only to --export.", file=sys.stderr)
            return 2
        return import_main(_import_argv(args))
    misused = _misused(args, _IMPORT_ONLY)
    if misused:
        print(f"error: {', '.join(misused)} apply only to --import.", file=sys.stderr)
        return 2
    return export_main(_export_argv(args))


if __name__ == "__main__":
    raise SystemExit(main())
