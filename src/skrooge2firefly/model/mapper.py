"""Transform Skrooge reader rows into the target-agnostic IR."""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal

from skrooge2firefly.model.currency import convert_amount, iso_code
from skrooge2firefly.model.entities import (
    Account,
    BudgetLimit,
    Currency,
    IRBudget,
    Recurrence,
    Split,
    Transaction,
)
from skrooge2firefly.skrooge import reader as sk

_PERIOD_UNIT_MAP: dict[str, str] = {"D": "daily", "W": "weekly", "M": "monthly", "Y": "yearly"}

# Firefly has no securities model, so a share purchase/sale is imported as a
# plain expense/income moving the cash correctly, tagged so it can be found and
# reclassified once Firefly supports securities. See _emit_security_leg.
_SECURITY_TAG = "security"
_SECURITY_ACCOUNT = "Securities"
_SECURITY_CATEGORY = "Securities"


def _has_valid_date(date: str) -> bool:
    """Report whether a date is real (not Skrooge's ``0000-00-00`` placeholder)."""
    return bool(date) and date != "0000-00-00"


# Used when an account's opening balance is derived from an invalid-date
# operation (see _fold_invalid_date_operations) and it has no other dated
# history to anchor the opening-balance date to.
_FALLBACK_OPENING_DATE = "1900-01-01"


# Skrooge account type → (Firefly kind, asset role or None, liability_type or None)
_ACCOUNT_TYPE_MAP: dict[str, tuple[str, str | None, str | None]] = {
    "C": ("asset", "defaultAsset", None),
    "S": ("asset", "savingAsset", None),
    "D": ("asset", "ccAsset", None),  # Skrooge D = credit card
    "W": ("asset", "cashWalletAsset", None),
    "I": ("asset", "defaultAsset", None),
    "O": ("asset", "defaultAsset", None),
    "L": ("liability", None, "loan"),
}


def _fold_invalid_date_operations(
    operations: list[sk.Operation], subs: dict[int, list[sk.Suboperation]]
) -> tuple[dict[int, Decimal], dict[int, int]]:
    """Sum non-transfer, invalid-dated operations per account.

    Skrooge encodes an account's opening balance as an operation dated
    ``0000-00-00`` when ``f_importbalance`` is empty; its own balance views
    have no date filter and count these unconditionally. Transfer-group
    operations (``group_id != 0``) are excluded here — they're handled by
    ``_map_transfers``, which already has its own invalid-date handling.

    Returns:
        (totals, counts): per-account-id summed value and operation count.

    """
    totals: dict[int, Decimal] = defaultdict(Decimal)
    counts: dict[int, int] = defaultdict(int)
    for op in operations:
        if op.template or op.group_id != 0 or _has_valid_date(op.date):
            continue
        for sub in subs.get(op.id, []):
            totals[op.account_id] += Decimal(str(sub.value))
        counts[op.account_id] += 1
    return dict(totals), dict(counts)


def _earliest_valid_date(operations: list[sk.Operation]) -> dict[int, str]:
    """Return each account's earliest real (non-placeholder) operation date."""
    earliest: dict[int, str] = {}
    for op in operations:
        if op.template or not _has_valid_date(op.date):
            continue
        current = earliest.get(op.account_id)
        if current is None or op.date < current:
            earliest[op.account_id] = op.date
    return earliest


@dataclass
class _Ctx:
    """Lookup tables shared across operation mapping."""

    units: dict[int, sk.Unit]
    accounts: dict[int, sk.Account]
    payees: dict[int, str]
    categories: dict[int, str]
    refunds: dict[int, str]
    subs: dict[int, list[sk.Suboperation]]
    account_currencies: dict[int, str]
    account_units: dict[int, int]  # account id -> dominant currency-unit id
    rates: dict[int, list[tuple[str, float]]]  # unit id -> (date, price in primary)
    primary_unit_id: int | None


@dataclass
class Mapper:
    """Holds the mapped IR collections."""

    currencies: list[Currency] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    budgets: list[IRBudget] = field(default_factory=list)
    recurrences: list[Recurrence] = field(default_factory=list)
    # Skrooge's own source-of-truth per-account summary (name -> AccountAudit),
    # read straight from the SQLite rows for the independent balance check in
    # verify(). Empty when a Mapper is constructed directly (e.g. in tests).
    account_audits: dict[str, sk.AccountAudit] = field(default_factory=dict)

    @classmethod
    def build(cls, reader: sk.SkroogeReader) -> Mapper:
        """Run the full mapping over a reader and return the populated IR."""
        m = cls()
        units = {u.id: u for u in reader.units()}
        m._map_currencies(units)
        m._primary_currency = next(  # type: ignore[attr-defined]
            (iso_code(u.symbol, u.name) for u in units.values() if u.is_primary), "EUR"
        )
        operations = reader.operations()
        subs = reader.suboperations_by_operation()
        account_currencies = m._infer_account_currencies(operations, units)
        account_units = m._infer_account_units(operations, units)
        opening_totals, opening_counts = _fold_invalid_date_operations(operations, subs)
        earliest_dates = _earliest_valid_date(operations)
        m._map_accounts(
            reader.accounts(), account_currencies, opening_totals, opening_counts, earliest_dates
        )

        accounts = {a.id: a for a in reader.accounts()}
        m._ctx = _Ctx(  # type: ignore[attr-defined]
            units=units,
            accounts=accounts,
            payees=reader.payees(),
            categories=reader.categories(),
            refunds=reader.refunds(),
            subs=subs,
            account_currencies=account_currencies,
            account_units=account_units,
            rates=reader.exchange_rates(),
            primary_unit_id=reader.primary_unit_id(),
        )
        m._map_operations(operations)
        m._map_budgets(reader.budgets(), m._ctx)  # type: ignore[attr-defined]
        m._map_recurrences(reader.recurrences(), reader.operations(), m._ctx)  # type: ignore[attr-defined]
        m.account_audits = reader.account_audits()
        return m

    def _map_currencies(self, units: dict[int, sk.Unit]) -> None:
        for unit in units.values():
            if not unit.is_currency:
                continue
            self.currencies.append(
                Currency(
                    code=iso_code(unit.symbol, unit.name),
                    name=unit.name,
                    symbol=unit.symbol,
                    decimal_places=unit.decimal_places,
                    primary=unit.is_primary,
                )
            )

    def _infer_account_currencies(
        self, operations: list[sk.Operation], units: dict[int, sk.Unit]
    ) -> dict[int, str]:
        """Infer each account's currency from the dominant currency of its operations."""
        counts: dict[int, Counter[str]] = defaultdict(Counter)
        for op in operations:
            unit = units.get(op.unit_id)
            if unit is not None and unit.is_currency:
                counts[op.account_id][iso_code(unit.symbol, unit.name)] += 1
        return {aid: c.most_common(1)[0][0] for aid, c in counts.items()}

    def _infer_account_units(
        self, operations: list[sk.Operation], units: dict[int, sk.Unit]
    ) -> dict[int, int]:
        """Infer each account's dominant currency-unit id (for FX conversion)."""
        counts: dict[int, Counter[int]] = defaultdict(Counter)
        for op in operations:
            unit = units.get(op.unit_id)
            if unit is not None and unit.is_currency:
                counts[op.account_id][op.unit_id] += 1
        return {aid: c.most_common(1)[0][0] for aid, c in counts.items()}

    def _book_amount(
        self, op: sk.Operation, account: sk.Account, amount: Decimal, ctx: _Ctx
    ) -> tuple[Decimal, str, Decimal | None, str | None]:
        """Return the amount in the account's own currency, plus foreign fields.

        Skrooge lets an operation be booked in any unit; Firefly needs each
        account movement in the account's own currency. When they differ, the
        amount is converted at Skrooge's historical rate (``unitvalue``) and the
        original is preserved as ``foreign_amount``/``foreign_currency_code`` so
        no information is lost. Returns ``(amount, currency, None, None)``
        unchanged when the units already match or no rate is available.
        """
        op_unit = ctx.units.get(op.unit_id)
        op_currency = iso_code(op_unit.symbol, op_unit.name) if op_unit else self._primary_currency  # type: ignore[attr-defined]
        dominant_unit_id = ctx.account_units.get(account.id, op.unit_id)
        dominant_unit = ctx.units.get(dominant_unit_id)
        if op.unit_id == dominant_unit_id or dominant_unit is None or ctx.primary_unit_id is None:
            return amount, op_currency, None, None
        converted = convert_amount(
            float(amount), op.unit_id, dominant_unit_id, ctx.primary_unit_id, ctx.rates, op.date
        )
        if converted is None:
            return amount, op_currency, None, None  # no rate: keep face value
        dom_currency = iso_code(dominant_unit.symbol, dominant_unit.name)
        return Decimal(str(round(converted, 2))), dom_currency, amount, op_currency

    def _map_accounts(
        self,
        accounts: list[sk.Account],
        account_currencies: dict[int, str],
        opening_totals: dict[int, Decimal],
        opening_counts: dict[int, int],
        earliest_dates: dict[int, str],
    ) -> None:
        # Skrooge accounts have no explicit currency; infer it from their operations,
        # falling back to the primary currency for accounts with no currency operations.
        primary = self._primary_currency  # type: ignore[attr-defined]
        for acct in accounts:
            kind, role, liability_type = _ACCOUNT_TYPE_MAP.get(
                acct.type, ("asset", "defaultAsset", None)
            )
            notes = f"Bank: {acct.bank_name}" if acct.bank_name else ""
            opening = Decimal(str(acct.import_balance)) if acct.import_balance is not None else None
            opening_date = acct.import_date
            if opening is None:
                total = opening_totals.get(acct.id)
                if total is not None and total != 0:
                    opening = total
                    opening_date = earliest_dates.get(acct.id) or _FALLBACK_OPENING_DATE
                    count = opening_counts.get(acct.id, 1)
                    self.warnings.append(
                        f"Account {acct.name}: {count} operation(s) with an invalid date "
                        f"totaling {total}; folded into opening balance "
                        f"(Skrooge encodes opening balances this way when "
                        f"f_importbalance is empty)."
                    )
            self.accounts.append(
                Account(
                    external_id=f"skrooge:acct:{acct.id}",
                    name=acct.name,
                    kind=kind,  # type: ignore[arg-type]
                    role=role,
                    currency_code=account_currencies.get(acct.id, primary),
                    opening_balance=opening,
                    opening_balance_date=opening_date,
                    liability_type=liability_type,
                    notes=notes,
                    active=not acct.closed,
                )
            )

    def _map_operations(self, operations: list[sk.Operation]) -> None:
        # Template operations are recurrence blueprints, not real transactions;
        # they are consumed by _map_recurrences, never imported as transactions.
        # Non-transfer invalid-date operations were already folded into their
        # account's opening balance by _fold_invalid_date_operations; transfer
        # legs (group_id != 0) keep their own invalid-date handling below.
        real_ops = [
            op
            for op in operations
            if not op.template and (op.group_id != 0 or _has_valid_date(op.date))
        ]
        ctx = self._ctx  # type: ignore[attr-defined]
        transfers: dict[int, list[sk.Operation]] = {}
        for op in real_ops:
            if op.group_id != 0:
                transfers.setdefault(op.group_id, []).append(op)

        for op in real_ops:
            if op.group_id != 0:
                continue  # handled in _map_transfers (Task 6)
            self._emit_operation(op, ctx)

        self._map_transfers(transfers, ctx)

    def _emit_operation(self, op: sk.Operation, ctx: _Ctx) -> None:
        """Emit one or two transaction groups for a non-transfer operation."""
        if not _has_valid_date(op.date):
            self.warnings.append(f"Skipped operation {op.id} with invalid date '{op.date}'.")
            return
        unit = ctx.units.get(op.unit_id)
        if unit is not None and not unit.is_currency:
            self.warnings.append(
                f"Skipped share-denominated operation {op.id} "
                f"(unit '{unit.name}') in investment account."
            )
            return
        subs = ctx.subs.get(op.id, [])
        if not subs:
            self.warnings.append(f"Operation {op.id} has no suboperations; skipped.")
            return
        account = ctx.accounts[op.account_id]
        payee = ctx.payees.get(op.payee_id, "")

        negatives = [s for s in subs if s.value < 0]
        positives = [s for s in subs if s.value >= 0]

        if negatives and positives:
            self._emit_group(op, account, payee, negatives, "withdrawal", ctx, suffix=":w")
            self._emit_group(op, account, payee, positives, "deposit", ctx, suffix=":d")
        elif negatives:
            self._emit_group(op, account, payee, negatives, "withdrawal", ctx, suffix="")
        else:
            self._emit_group(op, account, payee, positives, "deposit", ctx, suffix="")

    def _emit_group(
        self,
        op: sk.Operation,
        account: sk.Account,
        payee: str,
        subs: list[sk.Suboperation],
        kind: str,
        ctx: _Ctx,
        suffix: str,
    ) -> None:
        splits = [self._make_split(op, account, payee, sub, kind, ctx) for sub in subs]
        # Firefly renders amounts at 2 decimals and rejects 0.00, so sub-cent
        # splits (Skrooge float artifacts like 1e-07 "fake operations") must go.
        subcent = [s for s in splits if s.amount < Decimal("0.005")]
        if subcent:
            self.warnings.append(
                f"Operation {op.id}: dropped {len(subcent)} sub-cent split(s) (round to 0.00)."
            )
            splits = [s for s in splits if s.amount >= Decimal("0.005")]
        if not splits or sum((s.amount for s in splits), Decimal(0)) < Decimal("0.005"):
            return  # zero-value group; Firefly rejects amount-0 transactions
        group_title = op.comment if len(splits) > 1 else None
        self.transactions.append(
            Transaction(
                external_id=f"skrooge:op:{op.id}{suffix}",
                kind=kind,  # type: ignore[arg-type]
                date=op.date,
                splits=splits,
                group_title=group_title,
            )
        )

    def _make_split(
        self,
        op: sk.Operation,
        account: sk.Account,
        payee: str,
        sub: sk.Suboperation,
        kind: str,
        ctx: _Ctx,
    ) -> Split:
        amount = Decimal(str(abs(sub.value)))
        booked, booked_currency, foreign_amount, foreign_currency = self._book_amount(
            op, account, amount, ctx
        )
        category = ctx.categories.get(sub.category_id) if sub.category_id else None
        tags = (
            (ctx.refunds[sub.refund_id],) if sub.refund_id and sub.refund_id in ctx.refunds else ()
        )
        notes = sub.comment or op.comment
        payee_name = payee or "(unknown)"
        if kind == "withdrawal":
            source, destination = account.name, payee_name
        else:
            source, destination = payee_name, account.name
        return Split(
            amount=booked,
            currency_code=booked_currency,
            source_name=source,
            destination_name=destination,
            category_name=category,
            foreign_amount=foreign_amount,
            foreign_currency_code=foreign_currency,
            tags=tags,
            notes=notes,
            reconciled=op.status == "Y",
        )

    def _map_transfers(
        self,
        transfers: dict[int, list[sk.Operation]],
        ctx: _Ctx,
    ) -> None:
        """Collapse Skrooge transfer pairs into single Firefly transfers.

        A clean transfer is exactly two operations in different accounts with
        opposite-signed totals. Anything else (singletons, >2 legs) falls back
        to emitting each operation as a normal withdrawal/deposit, with a
        warning.
        """
        for group_id, ops in transfers.items():
            if len(ops) != 2:
                self.warnings.append(
                    f"Transfer group {group_id} has {len(ops)} operations; "
                    f"emitting each as a normal transaction."
                )
                for op in ops:
                    self._emit_operation(op, ctx)
                continue

            op_a, op_b = ops
            total_a = sum((Decimal(str(s.value)) for s in ctx.subs.get(op_a.id, [])), Decimal(0))
            total_b = sum((Decimal(str(s.value)) for s in ctx.subs.get(op_b.id, [])), Decimal(0))
            if not (total_a < 0 < total_b or total_b < 0 < total_a):
                self.warnings.append(
                    f"Transfer group {group_id} is not an opposite-signed pair; "
                    f"emitting each as a normal transaction."
                )
                for op in ops:
                    self._emit_operation(op, ctx)
                continue

            source_op, source_total = (op_a, total_a) if total_a < 0 else (op_b, total_b)
            dest_op, dest_total = (op_b, total_b) if total_a < 0 else (op_a, total_a)

            src_unit = ctx.units.get(source_op.unit_id)
            dst_unit = ctx.units.get(dest_op.unit_id)
            src_is_currency = src_unit is None or src_unit.is_currency
            dst_is_currency = dst_unit is None or dst_unit.is_currency
            if not src_is_currency or not dst_is_currency:
                self._emit_security_leg(
                    group_id,
                    cash_op=source_op if src_is_currency else dest_op,
                    cash_total=source_total if src_is_currency else dest_total,
                    share_op=dest_op if src_is_currency else source_op,
                    share_total=dest_total if src_is_currency else source_total,
                    share_unit=dst_unit if src_is_currency else src_unit,
                    cash_is_currency=src_is_currency or dst_is_currency,
                    ctx=ctx,
                )
                continue

            fallback = self._primary_currency  # type: ignore[attr-defined]
            src_currency = iso_code(src_unit.symbol, src_unit.name) if src_unit else fallback
            dst_currency = iso_code(dst_unit.symbol, dst_unit.name) if dst_unit else fallback
            source_account = ctx.accounts[source_op.account_id]
            dest_account = ctx.accounts[dest_op.account_id]

            if not _has_valid_date(source_op.date) or abs(source_total) == 0:
                self.warnings.append(
                    f"Transfer group {group_id} has an invalid date or zero amount; skipping."
                )
                continue

            # Firefly transfers demand currency_code == source-account currency and
            # foreign_currency == destination-account currency. A leg recorded in a
            # unit that is not its account's (inferred) currency cannot satisfy
            # that, so the pair falls back to two normal operations.
            src_acct_currency = ctx.account_currencies.get(source_op.account_id, src_currency)
            dst_acct_currency = ctx.account_currencies.get(dest_op.account_id, dst_currency)
            if src_currency != src_acct_currency or dst_currency != dst_acct_currency:
                self.warnings.append(
                    f"Transfer group {group_id}: leg currency differs from its account's "
                    f"currency; emitting each leg as a normal transaction."
                )
                for op in ops:
                    self._emit_operation(op, ctx)
                continue

            foreign_amount = None
            foreign_currency = None
            if src_currency != dst_currency:
                foreign_amount = abs(dest_total)
                foreign_currency = dst_currency
            elif abs(abs(source_total) - abs(dest_total)) > Decimal("0.01"):
                # Skrooge's two legs materially disagree (its own data is
                # inconsistent). A single transfer can only carry one amount, so
                # forcing them equal makes one account's balance wrong. Emit each
                # leg as its own operation instead, so BOTH accounts match their
                # own Skrooge-recorded amount.
                self.warnings.append(
                    f"Transfer group {group_id} legs differ materially "
                    f"({source_total} vs {dest_total}); emitting each leg separately "
                    f"so both account balances match Skrooge."
                )
                for op in ops:
                    self._emit_operation(op, ctx)
                continue

            # Firefly transfers are asset<->asset only. A pair that touches a
            # liability books as a withdrawal (paying the loan) or a deposit
            # (drawing from it); both-liability pairs fall back to normal ops.
            src_kind = _ACCOUNT_TYPE_MAP.get(source_account.type, ("asset", None, None))[0]
            dst_kind = _ACCOUNT_TYPE_MAP.get(dest_account.type, ("asset", None, None))[0]
            if src_kind == "liability" and dst_kind == "liability":
                self.warnings.append(
                    f"Transfer group {group_id} joins two liabilities; "
                    f"emitting each leg as a normal transaction."
                )
                for op in ops:
                    self._emit_operation(op, ctx)
                continue
            kind = "transfer"
            if dst_kind == "liability":
                kind = "withdrawal"
            elif src_kind == "liability":
                kind = "deposit"

            split = Split(
                amount=abs(source_total),
                currency_code=src_currency,
                source_name=source_account.name,
                destination_name=dest_account.name,
                foreign_amount=foreign_amount,
                foreign_currency_code=foreign_currency,
                notes=source_op.comment or dest_op.comment,
                reconciled=source_op.status == "Y" and dest_op.status == "Y",
            )
            self.transactions.append(
                Transaction(
                    external_id=f"skrooge:grp:{group_id}",
                    kind=kind,  # type: ignore[arg-type]
                    date=source_op.date,
                    splits=[split],
                )
            )

    def _emit_security_leg(
        self,
        group_id: int,
        *,
        cash_op: sk.Operation,
        cash_total: Decimal,
        share_op: sk.Operation,
        share_total: Decimal,
        share_unit: sk.Unit | None,
        cash_is_currency: bool,
        ctx: _Ctx,
    ) -> None:
        """Record the cash side of a share purchase/sale as a tagged expense/income.

        Firefly has no securities model, so a fund/share transfer is imported as
        a withdrawal (buy: cash leaves) or deposit (sell: cash arrives) to a
        ``Securities`` account, tagged ``security`` and carrying the share
        quantity/unit in its description, so the balances stay correct now and
        the entries can be reclassified once Firefly gains securities support.
        The share leg itself has no cash representation and is dropped.
        """
        if not cash_is_currency:
            self.warnings.append(
                f"Transfer group {group_id} moves shares between share accounts; "
                f"skipping (no cash leg to preserve)."
            )
            return
        if not _has_valid_date(cash_op.date) or abs(cash_total) == 0:
            self.warnings.append(
                f"Securities group {group_id}: zero-amount or undated cash leg; skipping."
            )
            return

        cash_account = ctx.accounts[cash_op.account_id]
        # Book in the account's own currency (foreign-currency cash legs — e.g.
        # NOK stock buys in a SEK account — are FX-converted, original kept).
        amount, currency, foreign_amount, foreign_currency = self._book_amount(
            cash_op, cash_account, abs(cash_total), ctx
        )
        kind = "withdrawal" if cash_total < 0 else "deposit"
        share_symbol = share_unit.symbol if share_unit else "shares"
        qty = abs(share_total)
        base = cash_op.comment or share_op.comment or "Securities"
        notes = f"{base} [{'bought' if kind == 'withdrawal' else 'sold'} {qty} {share_symbol}]"
        if kind == "withdrawal":
            source, destination = cash_account.name, _SECURITY_ACCOUNT
        else:
            source, destination = _SECURITY_ACCOUNT, cash_account.name
        split = Split(
            amount=amount,
            currency_code=currency,
            source_name=source,
            destination_name=destination,
            category_name=_SECURITY_CATEGORY,
            foreign_amount=foreign_amount,
            foreign_currency_code=foreign_currency,
            tags=(_SECURITY_TAG,),
            notes=notes,
            reconciled=cash_op.status == "Y",
        )
        self.transactions.append(
            Transaction(
                external_id=f"skrooge:grp:{group_id}",
                kind=kind,  # type: ignore[arg-type]
                date=cash_op.date,
                splits=[split],
            )
        )

    def _map_budgets(self, budgets: list[sk.Budget], ctx: _Ctx) -> None:
        by_name: dict[str, list[BudgetLimit]] = defaultdict(list)
        for b in budgets:
            name = ctx.categories.get(b.category_id)
            if not name or b.month < 1:
                continue  # skip yearly-aggregate rows (month 0) and orphan categories
            last = calendar.monthrange(b.year, b.month)[1]
            by_name[name].append(
                BudgetLimit(
                    start=f"{b.year:04d}-{b.month:02d}-01",
                    end=f"{b.year:04d}-{b.month:02d}-{last:02d}",
                    amount=Decimal(str(abs(b.budgeted))),
                )
            )
        for name, limits in by_name.items():
            self.budgets.append(IRBudget(name=name, limits=limits))

    def _map_recurrences(
        self, recurrences: list[sk.Recurrence], operations: list[sk.Operation], ctx: _Ctx
    ) -> None:
        ops_by_id = {op.id: op for op in operations}
        for rec in recurrences:
            op = ops_by_id.get(rec.operation_id)
            if op is None:
                continue
            subs = ctx.subs.get(op.id, [])
            if not subs:
                continue
            total = sum((Decimal(str(s.value)) for s in subs), Decimal(0))
            if total == 0:
                continue  # zero-amount; Firefly rejects amount-0 (mirrors _emit_group)
            kind = "withdrawal" if total < 0 else "deposit"
            unit = ctx.units.get(op.unit_id)
            if unit is not None and not unit.is_currency:
                continue
            currency = iso_code(unit.symbol, unit.name) if unit else self._primary_currency  # type: ignore[attr-defined]
            account = ctx.accounts[op.account_id]
            payee = ctx.payees.get(op.payee_id, "(unknown)")
            source, destination = (
                (account.name, payee) if kind == "withdrawal" else (payee, account.name)
            )
            category = ctx.categories.get(subs[0].category_id) if subs[0].category_id else None
            self.recurrences.append(
                Recurrence(
                    external_id=f"skrooge:rec:{rec.id}",
                    title=op.comment or f"{payee} ({account.name})",
                    kind=kind,  # type: ignore[arg-type]
                    first_date=rec.date,
                    repetition_type=_PERIOD_UNIT_MAP.get(rec.period_unit, "monthly"),
                    skip=max(rec.period_increment - 1, 0),
                    amount=abs(total),
                    description=op.comment or payee,
                    source_name=source,
                    destination_name=destination,
                    currency_code=currency,
                    category_name=category,
                )
            )
        self._disambiguate_recurrence_titles()

    def _disambiguate_recurrence_titles(self) -> None:
        """Make every recurrence title unique.

        Firefly *Bills* (subscriptions) have no external id and are matched by
        name, so two recurrences sharing a title (e.g. several ``Insurance``
        entries with different amounts) would collapse into one — all but the
        first silently dropped. Colliding titles get their amount/currency
        appended; any that still collide get a numeric suffix.
        """
        counts = Counter(r.title for r in self.recurrences)
        if all(n == 1 for n in counts.values()):
            return
        used: set[str] = set()
        result: list[Recurrence] = []
        for rec in self.recurrences:
            title = rec.title
            if counts[rec.title] > 1:
                title = f"{rec.title} ({rec.amount:.2f} {rec.currency_code})"
            base, i = title, 2
            while title in used:
                title = f"{base} #{i}"
                i += 1
            used.add(title)
            result.append(rec if title == rec.title else replace(rec, title=title))
        self.recurrences = result
