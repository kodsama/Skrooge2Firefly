"""Writer that loads the IR into Firefly-III via its REST API, idempotently."""

from __future__ import annotations

import calendar
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from skrooge2firefly.model.entities import Account, IRBudget, Recurrence, Split, Transaction
from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.writers import decisions
from skrooge2firefly.writers.base import WriteReport
from skrooge2firefly.writers.client import DuplicateTransactionError, FireflyClient, FireflyError
from skrooge2firefly.writers.orphans import FixedDecider, MappingDecider, OrphanDecider

# Marker written into a recurrence's notes when Skrooge2Firefly created it;
# only recurrences carrying it are ever eligible for orphan deletion.
_RECURRENCE_MARKER = "Imported from Skrooge recurring operation."

logger = logging.getLogger(__name__)

_ALL_SECTIONS = {"accounts", "transactions", "budgets", "recurrences"}

# Abort the run when this many transactions fail back-to-back: the server is
# down, and burning through the rest of the queue would only produce noise.
_MAX_CONSECUTIVE_FAILURES = 15


def _add_months(d: date, months: int) -> date:
    """Add ``months`` to ``d``, clamping the day to the target month's length."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def _next_first_date(anchor: str, repetition_type: str, increment: int, today: date) -> str:
    """Return the first occurrence strictly after ``today``.

    Firefly requires a recurring transaction's first execution date to be in the
    future; Skrooge anchors are historical. Step forward by whole ``increment``
    periods from the anchor so the original phase (day-of-month/weekday) is kept.
    """
    start = date.fromisoformat(anchor)
    increment = max(increment, 1)
    if start > today:
        return start.isoformat()
    k = 1
    while True:
        if repetition_type == "daily":
            cand = start + timedelta(days=increment * k)
        elif repetition_type == "weekly":
            cand = start + timedelta(weeks=increment * k)
        elif repetition_type == "yearly":
            cand = _add_months(start, 12 * increment * k)
        else:  # monthly (and any unknown type, matching the mapper default)
            cand = _add_months(start, increment * k)
        if cand > today:
            return cand.isoformat()
        k += 1


def _moment(anchor: str, repetition_type: str) -> str:
    """Return the Firefly ``repetitions[].moment`` for an anchor date."""
    d = date.fromisoformat(anchor)
    if repetition_type == "daily":
        return ""
    if repetition_type == "weekly":
        return str(d.isoweekday())
    if repetition_type == "yearly":
        return d.isoformat()
    return str(d.day)


def _amount(value: Decimal, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"


def _transaction_issue(payload: dict[str, Any]) -> str | None:
    """Return why Firefly would reject this transaction payload, or None if OK.

    Used by ``--dry-run`` as a pre-flight so problems (notably amounts that
    would round to 0.00 — the classic silent-import failure) are caught before
    anything is written.
    """
    splits = payload.get("transactions", [])
    if not splits:
        return "no splits"
    for i, split in enumerate(splits):
        try:
            amount = Decimal(str(split.get("amount")))
        except (InvalidOperation, TypeError):
            return f"split {i}: unparseable amount {split.get('amount')!r}"
        if amount <= 0:
            return f"split {i}: amount {amount} is not greater than 0 (Firefly rejects it)"
        if not split.get("source_name") or not split.get("destination_name"):
            return f"split {i}: missing source or destination account name"
        foreign = split.get("foreign_amount")
        if foreign is not None and not split.get("foreign_currency_code"):
            return f"split {i}: foreign_amount without foreign_currency_code"
    return None


class _Ledger:
    """An append-only text file of processed external_ids, one per line.

    Crash-safe: each ``record()`` call appends to disk immediately.
    O(1) membership checks via an in-memory set.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: set[str] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line:
                    self._seen.add(line)

    def has(self, external_id: str) -> bool:
        return external_id in self._seen

    def record(self, external_id: str) -> None:
        """Mark *external_id* as processed; append to disk if not already seen."""
        if external_id not in self._seen:
            self._seen.add(external_id)
            with self._path.open("a") as f:
                f.write(external_id + "\n")

    def discard(self, external_id: str) -> None:
        """Forget *external_id* (rewrites the ledger file from the in-memory set)."""
        if external_id in self._seen:
            self._seen.discard(external_id)
            self._path.write_text("".join(f"{e}\n" for e in sorted(self._seen)))


class FireflyApiWriter:
    """Creates currencies, accounts, transactions, budgets, and recurring transactions."""

    def __init__(
        self,
        client: FireflyClient,
        *,
        ledger_path: Path,
        dry_run: bool = False,
        strict: bool = False,
        assume_empty: bool = False,
        concurrency: int = 1,
        update: bool = False,
        orphan_decider: OrphanDecider | None = None,
        decisions_path: Path | None = None,
    ) -> None:
        """Create the writer.

        Args:
            client: A connected :class:`FireflyClient` (or compatible stub).
            ledger_path: Path to the idempotency ledger.
            dry_run: When True, build payloads and log but perform no writes.
            strict: When True, re-raise the first per-row error instead of collecting it.
            assume_empty: When True, skip reconciliation reads (target known fresh).
            concurrency: Number of parallel transaction POSTs (1 = sequential).
            update: When True, re-sync existing transactions and mirror account active state.
            orphan_decider: Decides whether to delete records present on the server but
                absent from the newer file. Defaults to always ignoring orphans.
            decisions_path: Path to the orphan-decisions plan file. When it exists, its
                decisions are applied without prompting (falling back to
                ``orphan_decider`` for keys it doesn't cover). On a dry run, the
                decisions made this run are written back to this path.

        """
        self._client = client
        self._ledger = _Ledger(ledger_path)
        self._dry_run = dry_run
        self._strict = strict
        self._assume_empty = assume_empty
        self._concurrency = concurrency
        self._update = update
        self._decisions_path = decisions_path
        self._orphan_choices: dict[str, str] = {}
        base_decider = orphan_decider or FixedDecider("ignore")
        if decisions_path is not None and decisions_path.exists():
            self._orphan_decider: OrphanDecider = MappingDecider(
                decisions.load(decisions_path), fallback=base_decider
            )
        else:
            self._orphan_decider = base_decider
        self._existing_group_ids: dict[str, str] = {}
        self._account_states: dict[str, tuple[str, bool]] = {}
        self._account_ids: dict[str, str] = {}
        self._account_index: dict[str, str] = {}
        self._budget_index: dict[str, str] = {}
        self._existing_txn_ids: set[str] = set()
        self._existing_txn_content: dict[str, dict[str, Any]] = {}
        self._recurrence_index: dict[str, str] = {}
        self._existing_recurrences: dict[str, dict[str, Any]] = {}
        self._existing_budget_limits: dict[str, set[tuple[str, str, Any]]] = {}
        self._consecutive_failures = 0
        self._account_active: dict[str, bool] = {}  # live remote active-state by name
        self._currency_decimals: dict[str, int] = {}
        self._existing_account_attrs: dict[str, dict[str, Any]] = {}

    def _fmt(self, value: Decimal, currency: str) -> str:
        """Format an amount at its currency's decimal places (default 2)."""
        return _amount(value, self._currency_decimals.get(currency, 2))

    def _note_transaction_failure(self) -> None:
        """Count a failure; abort the run when the server looks dead."""
        self._consecutive_failures += 1
        logger.warning("transaction failure %d in a row", self._consecutive_failures)
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            raise FireflyError(
                f"Aborting: {self._consecutive_failures} consecutive transaction failures — "
                "the server appears to be down. The run is resumable; try again later."
            )

    def write(self, mapper: Mapper, *, only: set[str] | None = None) -> WriteReport:
        """Write selected IR sections to Firefly and return a report."""
        sections = only or _ALL_SECTIONS
        report = WriteReport()
        # Format amounts at the SERVER's decimal places: Firefly re-rounds
        # whatever it receives at its own precision (with a different rounding
        # mode), so matching it exactly is the only way verify can mirror it.
        self._currency_decimals = {c.code: c.decimal_places for c in mapper.currencies}
        # These reads are safe (read-only) in --dry-run too, so a pre-flight can
        # report accurate "would create" vs "already present" counts and format
        # amounts exactly as the real run would.
        try:
            self._currency_decimals.update(self._client.currency_decimals())
        except AttributeError:  # older stubs without the method
            pass
        if not self._assume_empty or self._update:
            self._reconcile()
        self._ensure_currencies(mapper, report)
        if "accounts" in sections:
            self._write_accounts(mapper, report)
        if "transactions" in sections:
            if not self._dry_run:
                self._activate_accounts_for_writes(mapper)
            self._write_transactions(mapper, report)
        if "budgets" in sections:
            self._write_budgets(mapper, report)
        if "recurrences" in sections:
            self._write_recurrences(mapper, report)
        if self._update:
            self._resolve_orphans(mapper, sections, report)
        if not self._dry_run:
            self._restore_account_states(mapper)
        return report

    def _activate_accounts_for_writes(self, mapper: Mapper) -> None:
        """Temporarily activate inactive accounts.

        Firefly refuses to resolve inactive accounts as transaction source or
        destination, so closed Skrooge accounts must be active while their
        history is written; :meth:`_restore_account_states` closes them again.
        """
        for acct in mapper.accounts:
            account_id = self._account_ids.get(acct.name)
            if account_id is None or self._account_active.get(acct.name, True):
                continue
            self._client.update_account(account_id, {"name": acct.name, "active": True})
            self._account_active[acct.name] = True
            logger.info("Temporarily activated closed account %s for import", acct.name)

    def _restore_account_states(self, mapper: Mapper) -> None:
        """Re-apply the desired closed state after all writes are done."""
        for acct in mapper.accounts:
            account_id = self._account_ids.get(acct.name)
            if acct.active or account_id is None:
                continue
            if not self._account_active.get(acct.name, True):
                continue  # already inactive remotely
            self._client.update_account(account_id, {"name": acct.name, "active": False})
            self._account_active[acct.name] = False
            logger.info("Restored closed state of account %s", acct.name)

    def _reconcile(self) -> None:
        """Fetch existing state from Firefly so we reuse/skip instead of duplicating."""
        from skrooge2firefly.writers.diffing import norm_amount

        self._account_index = self._client.account_index()
        self._budget_index = self._client.budget_index()
        self._existing_txn_ids = self._client.existing_external_ids()
        self._recurrence_index = self._client.recurrence_index()
        self._account_states = self._client.account_states("asset")
        self._account_states.update(self._client.account_states("liability"))
        self._account_active = {name: active for name, (_, active) in self._account_states.items()}
        for name, (account_id, _) in self._account_states.items():
            self._account_ids.setdefault(name, account_id)
        if self._update:
            self._existing_account_attrs = {
                **self._client.accounts_full("asset"),
                **self._client.accounts_full("liability"),
            }
            self._existing_group_ids = self._client.external_id_to_group_id()
            self._existing_txn_content = self._client.transactions_by_external_id()
            self._existing_recurrences = self._client.recurrences_full()
            self._existing_budget_limits = {
                name: {
                    (
                        a["attributes"]["start"],
                        a["attributes"]["end"],
                        norm_amount(a["attributes"]["amount"]),
                    )
                    for it in limits
                    for a in [it]
                }
                for name, limits in self._client.list_budgets_with_limits()
            }
        logger.info(
            "Reconciled target: %d accounts, %d budgets, %d skrooge transactions, %d recurrences",
            len(self._account_index),
            len(self._budget_index),
            len(self._existing_txn_ids),
            len(self._recurrence_index),
        )

    def _ensure_currencies(self, mapper: Mapper, report: WriteReport) -> None:
        existing = self._client.list_currency_codes() if not self._dry_run else set()
        seen: set[str] = set()
        for cur in mapper.currencies:
            if cur.code in existing or cur.code in seen:
                continue
            seen.add(cur.code)
            payload = {
                "code": cur.code,
                "name": cur.name,
                "symbol": cur.symbol or cur.code,
                "decimal_places": cur.decimal_places,
                "enabled": True,
            }
            if self._dry_run:
                report.created("currency")  # pre-flight: would be created
                continue
            self._client.store_currency(payload)
            report.created("currency")

    def _write_accounts(self, mapper: Mapper, report: WriteReport) -> None:
        from skrooge2firefly.writers.diffing import norm_str

        managed_keys = (
            "currency_code",
            "notes",
            "account_role",
            "liability_type",
            "liability_direction",
        )
        for acct in mapper.accounts:
            if self._update and acct.name in self._account_states:
                account_id, current_active = self._account_states[acct.name]
                self._account_ids[acct.name] = account_id
                self._ledger.record(acct.external_id)
                # _account_payload hardcodes active=True (needed so freshly
                # created accounts can immediately book transactions); an
                # already-existing account being patched here isn't going
                # through that create flow, so send its real desired state.
                payload = self._account_payload(acct)
                payload["active"] = acct.active
                existing_attrs = self._existing_account_attrs.get(acct.name, {}).get(
                    "attributes", {}
                )
                fields_differ = any(
                    norm_str(payload.get(k)) != norm_str(existing_attrs.get(k))
                    for k in managed_keys
                )
                needs_update = current_active != acct.active or fields_differ
                if needs_update and not self._dry_run:
                    try:
                        self._client.update_account(account_id, payload)
                        logger.info("Account %s updated", acct.name)
                        report.updated("account")
                    except Exception as exc:  # noqa: BLE001
                        report.failed("account", f"{acct.name}: {exc}")
                        if self._strict:
                            raise
                elif needs_update:
                    report.updated("account")  # dry-run: would update
                else:
                    report.skipped("account")
                continue
            if self._ledger.has(acct.external_id):
                report.skipped("account")
                continue
            if acct.name in self._account_index:
                self._account_ids[acct.name] = self._account_index[acct.name]
                self._ledger.record(acct.external_id)
                report.skipped("account")
                continue
            payload = self._account_payload(acct)
            if self._dry_run:
                report.created("account")  # pre-flight: would be created
                continue
            try:
                fid = self._client.store_account(payload)
                self._account_ids[acct.name] = fid
                self._account_index[acct.name] = fid
                self._ledger.record(acct.external_id)
                report.created("account")
            except Exception as exc:  # noqa: BLE001 - collected, run continues
                try:
                    existing_fid = self._client.find_account_id(acct.name)
                except Exception:  # noqa: BLE001 - lookup failed; treat as create failure
                    existing_fid = None
                if existing_fid is not None:
                    self._account_ids[acct.name] = existing_fid
                    self._account_index[acct.name] = existing_fid
                    self._ledger.record(acct.external_id)
                    report.skipped("account")
                else:
                    report.failed("account", f"{acct.name}: {exc}")
                    if self._strict:
                        raise

    def _account_payload(self, acct: Account) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": acct.name,
            "type": acct.kind,
            "currency_code": acct.currency_code,
            "notes": acct.notes or None,
            # Created active even when closed in Skrooge: Firefly cannot book
            # transactions against inactive accounts. _restore_account_states
            # applies the real closed state once all writes are done.
            "active": True,
        }
        if acct.role:
            payload["account_role"] = acct.role
            if acct.role == "ccAsset":
                # Firefly refuses ccAsset accounts without these two fields.
                payload["credit_card_type"] = "monthlyFull"
                payload["monthly_payment_date"] = "2020-01-01"
        if acct.opening_balance is not None and acct.opening_balance_date:
            payload["opening_balance"] = _amount(acct.opening_balance)
            payload["opening_balance_date"] = acct.opening_balance_date
        if acct.kind == "liability":
            payload["liability_type"] = acct.liability_type or "loan"
            # Firefly only respects the sign of `opening_balance` for a
            # liability when liability_direction is "debit"; "credit"
            # silently forces it to abs(value) (confirmed empirically against
            # a live instance: both +N and -N produced a positive balance
            # under "credit", while "debit" reproduced the sign given).
            # Direction doesn't affect how ordinary transactions are booked,
            # so "credit" (the normal "this account owes the debt" case)
            # remains the default when there's no opening balance to apply.
            payload["liability_direction"] = (
                "debit" if acct.opening_balance is not None else "credit"
            )
        return payload

    def _write_transactions(self, mapper: Mapper, report: WriteReport) -> None:
        if self._update:
            self._update_transactions(mapper, report)
            return
        pending: list[Transaction] = []
        for txn in mapper.transactions:
            if txn.external_id in self._existing_txn_ids or self._ledger.has(txn.external_id):
                report.skipped("transaction")
            else:
                pending.append(txn)
        if self._dry_run:
            for txn in pending:
                issue = _transaction_issue(self._transaction_payload(txn))
                if issue is not None:
                    report.failed("transaction", f"{txn.external_id}: {issue}")
                else:
                    report.created("transaction")  # pre-flight: would be created
            return
        if self._concurrency <= 1:
            for txn in pending:
                self._post_one_transaction(txn, report)
            return
        pool = ThreadPoolExecutor(max_workers=self._concurrency)
        try:
            futures = {
                pool.submit(self._client.store_transaction, self._transaction_payload(txn)): txn
                for txn in pending
            }
            for fut in as_completed(futures):
                txn = futures[fut]
                try:
                    fut.result()
                    self._ledger.record(txn.external_id)
                    report.created("transaction")
                    self._consecutive_failures = 0
                except DuplicateTransactionError:
                    self._ledger.record(txn.external_id)
                    report.skipped("transaction")
                    self._consecutive_failures = 0
                except Exception as exc:  # noqa: BLE001
                    logger.warning("transaction %s failed: %s", txn.external_id, exc)
                    report.failed("transaction", f"{txn.external_id}: {exc}")
                    if self._strict:
                        raise
                    self._note_transaction_failure()
        finally:
            # Cancel queued work on abort; don't wait out 17k doomed POSTs.
            pool.shutdown(wait=False, cancel_futures=True)

    def _post_one_transaction(self, txn: Transaction, report: WriteReport) -> None:
        try:
            self._client.store_transaction(self._transaction_payload(txn))
            self._ledger.record(txn.external_id)
            report.created("transaction")
            self._consecutive_failures = 0
        except DuplicateTransactionError:
            self._ledger.record(txn.external_id)
            report.skipped("transaction")
            self._consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("transaction %s failed: %s", txn.external_id, exc)
            report.failed("transaction", f"{txn.external_id}: {exc}")
            if self._strict:
                raise
            self._note_transaction_failure()

    def _update_transactions(self, mapper: Mapper, report: WriteReport) -> None:
        for txn in mapper.transactions:
            group_id = self._existing_group_ids.get(txn.external_id)
            if group_id is None:
                if self._dry_run:
                    report.created("transaction")
                else:
                    self._post_one_transaction(txn, report)
                continue
            existing = self._existing_txn_content.get(txn.external_id, {})
            if not self._txn_changed(txn, existing):
                self._ledger.record(txn.external_id)
                report.skipped("transaction")
                continue
            if self._dry_run:
                report.updated("transaction")
                continue
            try:
                self._client.update_transaction(group_id, self._update_payload(txn))
                self._ledger.record(txn.external_id)
                report.updated("transaction")
                self._consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                report.failed("transaction", f"{txn.external_id}: {exc}")
                if self._strict:
                    raise
                self._note_transaction_failure()

    def _txn_changed(self, txn: Transaction, existing: dict[str, Any]) -> bool:
        """Return True if any managed field differs from the transaction on the server."""
        from skrooge2firefly.writers.diffing import norm_amount, norm_date, norm_str

        desired = self._update_payload(txn)["transactions"]
        current = existing.get("splits", [])
        if len(desired) != len(current):
            return True
        for d, c in zip(desired, current, strict=True):
            if (
                norm_str(d.get("type")) != norm_str(c.get("type"))
                or norm_date(str(d.get("date"))) != norm_date(str(c.get("date")))
                or norm_amount(d["amount"]) != norm_amount(c.get("amount", "0"))
                or norm_str(d.get("currency_code")) != norm_str(c.get("currency_code"))
                or norm_str(d.get("description")) != norm_str(c.get("description"))
                or norm_str(d.get("source_name")) != norm_str(c.get("source_name"))
                or norm_str(d.get("destination_name")) != norm_str(c.get("destination_name"))
                or norm_str(d.get("category_name")) != norm_str(c.get("category_name"))
            ):
                return True
        return False

    def _update_payload(self, txn: Transaction) -> dict[str, Any]:
        splits = [self._split_payload(txn, s, i) for i, s in enumerate(txn.splits)]
        payload: dict[str, Any] = {"transactions": splits}
        if len(splits) > 1:
            payload["group_title"] = (
                txn.group_title or splits[0]["description"] or f"Skrooge {txn.external_id}"
            )
        return payload

    def _transaction_payload(self, txn: Transaction) -> dict[str, Any]:
        splits = [self._split_payload(txn, s, i) for i, s in enumerate(txn.splits)]
        payload: dict[str, Any] = {
            "error_if_duplicate_hash": False,
            "apply_rules": False,
            "fire_webhooks": False,
            "transactions": splits,
        }
        if len(splits) > 1:
            payload["group_title"] = (
                txn.group_title or splits[0]["description"] or f"Skrooge {txn.external_id}"
            )
        return payload

    def _split_payload(self, txn: Transaction, split: Split, order: int) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": txn.kind,
            "date": txn.date,
            "amount": self._fmt(split.amount, split.currency_code),
            "description": self._describe(txn, split),
            "currency_code": split.currency_code,
            "source_name": split.source_name,
            "destination_name": split.destination_name,
            "external_id": txn.external_id,
            "reconciled": split.reconciled,
            "order": order,
        }
        if split.category_name:
            payload["category_name"] = split.category_name
        if split.tags:
            payload["tags"] = list(split.tags)
        if split.foreign_amount is not None and split.foreign_currency_code:
            payload["foreign_amount"] = self._fmt(split.foreign_amount, split.foreign_currency_code)
            payload["foreign_currency_code"] = split.foreign_currency_code
        return payload

    def _describe(self, txn: Transaction, split: Split) -> str:
        """Return a meaningful, non-empty transaction description.

        Prefers the operation's own note; otherwise derives one from the
        counterparty (payee) combined with the category leaf (e.g.
        "Winston Craft Cafes & Bar") so a transaction is never titled with only
        a generic placeholder.
        """
        if split.notes:
            return split.notes
        if txn.kind == "transfer":
            return f"Transfer: {split.source_name} → {split.destination_name}"
        counterparty = split.destination_name if txn.kind == "withdrawal" else split.source_name
        if counterparty == "(unknown)":
            counterparty = ""
        category_leaf = split.category_name.split(" > ")[-1] if split.category_name else ""
        parts = [p for p in (counterparty, category_leaf) if p]
        return " ".join(parts) if parts else "(no description)"

    def _write_budgets(self, mapper: Mapper, report: WriteReport) -> None:
        for budget in mapper.budgets:
            ext = f"skrooge:budget:{budget.name}"
            if self._ledger.has(ext) and not self._update:
                report.skipped("budget")
                continue
            if budget.name in self._budget_index:
                self._ledger.record(ext)
                if self._update:
                    self._sync_budget_limits(budget, report)
                else:
                    report.skipped("budget")
                continue
            if self._dry_run:
                report.created("budget")  # pre-flight: would be created
                continue
            try:
                bid = self._client.store_budget({"name": budget.name, "active": True})
                for limit in budget.limits:
                    self._client.store_budget_limit(
                        bid,
                        {"start": limit.start, "end": limit.end, "amount": _amount(limit.amount)},
                    )
                self._budget_index[budget.name] = bid
                self._ledger.record(ext)
                report.created("budget")
            except Exception as exc:  # noqa: BLE001
                report.failed("budget", f"{budget.name}: {exc}")
                if self._strict:
                    raise

    def _sync_budget_limits(self, budget: IRBudget, report: WriteReport) -> None:
        """Post any of ``budget``'s limits missing on the server; report the outcome."""
        from skrooge2firefly.writers.diffing import norm_amount

        existing = self._existing_budget_limits.get(budget.name, set())
        desired = [
            (limit, (limit.start, limit.end, norm_amount(_amount(limit.amount))))
            for limit in budget.limits
        ]
        missing = [limit for limit, key in desired if key not in existing]
        if not missing:
            report.skipped("budget")
            return
        if self._dry_run:
            report.updated("budget")  # pre-flight: would be updated
            return
        budget_id = self._budget_index[budget.name]
        try:
            for limit in missing:
                self._client.store_budget_limit(
                    budget_id,
                    {"start": limit.start, "end": limit.end, "amount": _amount(limit.amount)},
                )
            report.updated("budget")
        except Exception as exc:  # noqa: BLE001
            report.failed("budget", f"{budget.name}: {exc}")
            if self._strict:
                raise

    def _write_recurrences(self, mapper: Mapper, report: WriteReport) -> None:
        for rec in mapper.recurrences:
            if self._ledger.has(rec.external_id) and not self._update:
                report.skipped("recurrence")
                continue
            existing = self._existing_recurrences.get(rec.title) if self._update else None
            if rec.title in self._recurrence_index or existing is not None:
                if (
                    self._update
                    and existing is not None
                    and self._recurrence_changed(rec, existing)
                ):
                    if self._dry_run:
                        report.updated("recurrence")  # pre-flight: would be updated
                        continue
                    try:
                        self._client.update_recurrence(
                            existing["id"], self._recurrence_payload(rec)
                        )
                        report.updated("recurrence")
                    except Exception as exc:  # noqa: BLE001
                        report.failed("recurrence", f"{rec.title}: {exc}")
                        if self._strict:
                            raise
                    continue
                report.skipped("recurrence")
                continue
            if self._dry_run:
                report.created("recurrence")  # pre-flight: would be created
                continue
            payload = self._recurrence_payload(rec)
            try:
                rid = self._client.store_recurrence(payload)
                self._recurrence_index[rec.title] = rid
                self._ledger.record(rec.external_id)
                report.created("recurrence")
            except Exception as exc:  # noqa: BLE001
                report.failed("recurrence", f"{rec.title}: {exc}")
                if self._strict:
                    raise

    def _recurrence_payload(self, rec: Recurrence) -> dict[str, Any]:
        today = date.today()
        increment = rec.skip + 1
        payload: dict[str, Any] = {
            "type": rec.kind,
            "title": rec.title,
            "description": rec.description,
            "first_date": _next_first_date(rec.first_date, rec.repetition_type, increment, today),
            "apply_rules": False,
            "active": True,
            "notes": _RECURRENCE_MARKER,
            "repetitions": [
                {
                    "type": rec.repetition_type,
                    "moment": _moment(rec.first_date, rec.repetition_type),
                    "skip": rec.skip,
                }
            ],
        }
        txn: dict[str, Any] = {
            "description": rec.description,
            "amount": _amount(rec.amount),
            "currency_code": rec.currency_code,
            "source_name": rec.source_name,
            "destination_name": rec.destination_name,
        }
        if rec.category_name:
            txn["category_name"] = rec.category_name
        payload["transactions"] = [txn]
        return payload

    def _recurrence_changed(self, rec: Recurrence, existing: dict[str, Any]) -> bool:
        """Return True if any managed field differs (excludes first_date and description)."""
        from skrooge2firefly.writers.diffing import norm_amount, norm_str

        attrs = existing.get("attributes", {})
        reps = attrs.get("recurrence_repetitions", [{}])
        rep = reps[0] if reps else {}
        etx = (attrs.get("transactions") or [{}])[0]
        etype = (attrs.get("transaction_type") or {}).get("type", "")
        return (
            norm_str(rec.kind) != norm_str(etype)
            or norm_amount(_amount(rec.amount)) != norm_amount(etx.get("amount", "0"))
            or norm_str(rec.currency_code) != norm_str(etx.get("currency_code"))
            or norm_str(rec.source_name) != norm_str(etx.get("source_name"))
            or norm_str(rec.destination_name) != norm_str(etx.get("destination_name"))
            or norm_str(rec.repetition_type) != norm_str(rep.get("type"))
            or norm_str(_moment(rec.first_date, rec.repetition_type)) != norm_str(rep.get("moment"))
            or int(rec.skip) != int(rep.get("skip", 0) or 0)
        )

    def _resolve_orphans(self, mapper: Mapper, sections: set[str], report: WriteReport) -> None:
        """Delete or ignore records present on the server but missing from the file.

        Only transactions (via ``skrooge:`` external_id) and recurrences (via the
        Skrooge notes marker) carry an ownership marker; budgets and accounts have
        none and are never candidates for deletion here.
        """
        if "transactions" in sections:
            self._resolve_orphan_transactions(mapper, report)
        if "recurrences" in sections:
            self._resolve_orphan_recurrences(mapper, report)
        if self._decisions_path is not None and self._dry_run:
            merged = decisions.load(self._decisions_path)  # preserve prior partial-run choices
            merged.update(self._orphan_choices)  # this run's choices win on conflict
            decisions.save(self._decisions_path, merged)

    def _resolve_orphan_transactions(self, mapper: Mapper, report: WriteReport) -> None:
        mapped_ids = {t.external_id for t in mapper.transactions}
        orphan_keys = set(self._existing_group_ids) - mapped_ids
        for key in orphan_keys:
            group_id = self._existing_group_ids[key]
            content = self._existing_txn_content.get(key)
            splits = content.get("splits") if content else None
            if splits:
                split = splits[0]
                label = f"{split.get('description', '')} {split.get('amount', '')}".strip()
            else:
                label = key
            action = self._orphan_decider.decide("transaction", key, label)
            self._orphan_choices[key] = action
            if action == "delete":
                report.deleted("orphan-transaction")
                if not self._dry_run:
                    self._client.delete_transaction(group_id)
                    self._ledger.discard(key)
            else:
                report.skipped("orphan-transaction")

    def _resolve_orphan_recurrences(self, mapper: Mapper, report: WriteReport) -> None:
        mapped_titles = {r.title for r in mapper.recurrences}
        candidates = {
            title
            for title, rec in self._existing_recurrences.items()
            if _RECURRENCE_MARKER in rec.get("attributes", {}).get("notes", "")
        }
        for title in candidates - mapped_titles:
            recurrence_id = self._existing_recurrences[title]["id"]
            key = f"rec:{title}"
            action = self._orphan_decider.decide("recurrence", key, title)
            self._orphan_choices[key] = action
            if action == "delete":
                report.deleted("orphan-recurrence")
                if not self._dry_run:
                    self._client.delete_recurrence(recurrence_id)
                    # No ledger discard here: recurrences are keyed by title, not
                    # Skrooge external_id, and the deleted record's external_id
                    # isn't available at this point (it's absent from the mapper).
            else:
                report.skipped("orphan-recurrence")
