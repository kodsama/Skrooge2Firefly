from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from skrooge2firefly.model.entities import (
    Account,
    BudgetLimit,
    Currency,
    IRBudget,
    Recurrence,
    Split,
    Transaction,
)
from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.writers.client import DuplicateTransactionError, FireflyError
from skrooge2firefly.writers.firefly_api import FireflyApiWriter


class FakeClient:
    """In-memory stand-in for FireflyClient."""

    def __init__(self) -> None:
        self.existing_currencies = {"SEK"}
        self.stored_currencies: list[dict] = []
        self.stored_accounts: list[dict] = []
        self.stored_transactions: list[dict] = []
        self.stored_budgets: list[dict] = []
        self.stored_budget_limits: list[tuple[str, dict]] = []
        self.stored_recurrences: list[dict] = []
        self.updated_transactions: list[tuple] = []
        self.updated_accounts: list[tuple] = []
        self.existing_account_attrs: dict = {}
        self.duplicate_next = False
        self.existing_txn_content: dict = {}
        self.existing_recurrence_content: dict = {}
        self.updated_recurrences: list = []
        self.deleted_transactions: list = []
        self.deleted_recurrences: list = []
        # Map used by find_account_id
        self._account_name_to_id: dict[str, str] = {"Checking": "10", "Landlord": "20"}

    def get_version(self) -> str:
        return "6.2.0"

    def list_currency_codes(self) -> set[str]:
        return set(self.existing_currencies)

    def store_currency(self, payload: dict) -> None:
        self.stored_currencies.append(payload)

    def store_account(self, payload: dict) -> str:
        self.stored_accounts.append(payload)
        return str(len(self.stored_accounts))

    def store_transaction(self, payload: dict) -> str:
        if self.duplicate_next:
            raise DuplicateTransactionError("Duplicate")
        self.stored_transactions.append(payload)
        return str(len(self.stored_transactions))

    def store_budget(self, payload: dict) -> str:
        self.stored_budgets.append(payload)
        return str(len(self.stored_budgets))

    def store_budget_limit(self, budget_id: str, payload: dict) -> None:
        self.stored_budget_limits.append((budget_id, payload))

    def store_recurrence(self, payload: dict) -> str:
        self.stored_recurrences.append(payload)
        return str(len(self.stored_recurrences))

    def recurrence_index(self) -> dict[str, str]:
        return dict(getattr(self, "existing_recurrences", {}))

    def recurrences_full(self) -> dict:
        return dict(getattr(self, "existing_recurrence_content", {}))

    def update_recurrence(self, rid: str, payload: dict) -> None:
        self.updated_recurrences.append((rid, payload))

    def find_account_id(self, name: str) -> str | None:
        return self._account_name_to_id.get(name)

    # --- reconciliation reads ---
    def account_index(self) -> dict:
        return dict(getattr(self, "existing_accounts", {}))

    def budget_index(self) -> dict:
        return dict(getattr(self, "existing_budgets", {}))

    def list_budgets_with_limits(self) -> list:
        return getattr(self, "existing_budget_limits_raw", [])

    def existing_external_ids(self, prefix: str = "skrooge:") -> set:
        return set(getattr(self, "existing_txn_ids", set()))

    def external_id_to_group_id(self, prefix: str = "skrooge:") -> dict:
        return dict(getattr(self, "existing_group_ids", {}))

    def transactions_by_external_id(self, prefix: str = "skrooge:") -> dict:
        return dict(getattr(self, "existing_txn_content", {}))

    def account_states(self, account_type: str) -> dict:
        return dict(getattr(self, "account_state_map", {})) if account_type == "asset" else {}

    def accounts_full(self, account_type: str) -> dict:
        return dict(getattr(self, "existing_account_attrs", {})) if account_type == "asset" else {}

    def update_transaction(self, group_id: str, payload: dict) -> None:
        self.updated_transactions.append((group_id, payload))

    def update_account(self, account_id: str, payload: dict) -> None:
        self.updated_accounts.append((account_id, payload))

    def delete_transaction(self, group_id: str) -> None:
        self.deleted_transactions.append(group_id)

    def delete_recurrence(self, recurrence_id: str) -> None:
        self.deleted_recurrences.append(recurrence_id)


def _mapper_with_one_txn() -> Mapper:
    m = Mapper()
    m.currencies = [Currency("EUR", "Euro", "€", 2, False), Currency("SEK", "kr", "SEK", 2, True)]
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            Decimal("100"),
            "2010-01-01",
            None,
            "Bank: SEB",
        ),
    ]
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("12.50"), "SEK", "Checking", "ICA", category_name="Food")],
        ),
    ]
    return m


def test_currencies_only_missing_are_created(tmp_path: Path):
    client = FakeClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "state.json")
    writer.write(_mapper_with_one_txn(), only={"accounts", "transactions"})
    # SEK already exists; only EUR should be stored.
    assert [c["code"] for c in client.stored_currencies] == ["EUR"]


def test_transaction_payload_shape(tmp_path: Path):
    client = FakeClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "state.json")
    writer.write(_mapper_with_one_txn())
    payload = client.stored_transactions[0]
    assert payload["error_if_duplicate_hash"] is False
    split = payload["transactions"][0]
    assert split["type"] == "withdrawal"
    assert split["amount"] == "12.50"
    assert split["source_name"] == "Checking"
    assert split["destination_name"] == "ICA"
    assert split["external_id"] == "skrooge:op:1"


def test_ledger_skips_already_created_on_rerun(tmp_path: Path):
    ledger = tmp_path / "state.json"
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=ledger).write(_mapper_with_one_txn())
    assert len(client.stored_transactions) == 1
    # Re-run with a fresh client: ledger should prevent re-sending.
    client2 = FakeClient()
    report = FireflyApiWriter(client2, ledger_path=ledger).write(_mapper_with_one_txn())
    assert len(client2.stored_transactions) == 0
    assert report.counts["transaction"]["skipped"] == 1


def test_duplicate_hash_is_counted_as_skip(tmp_path: Path):
    client = FakeClient()
    client.duplicate_next = True
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(
        _mapper_with_one_txn(), only={"transactions"}
    )
    assert report.counts["transaction"]["skipped"] == 1


# Fix 2 (A5): ledger file is plain-text, one external_id per line
def test_ledger_file_written_after_write(tmp_path: Path):
    ledger = tmp_path / "state.json"
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=ledger).write(_mapper_with_one_txn())
    assert ledger.exists()
    lines = [ln.strip() for ln in ledger.read_text().splitlines() if ln.strip()]
    assert "skrooge:op:1" in lines


# Fix 3: budget correctness
def _mapper_with_budget() -> Mapper:
    m = Mapper()
    m.currencies = [Currency("EUR", "Euro", "€", 2, True)]
    m.accounts = []
    m.transactions = []
    m.budgets = [
        IRBudget(
            name="Groceries",
            limits=[BudgetLimit(start="2020-06-01", end="2020-06-30", amount=Decimal("500"))],
        )
    ]
    m.recurrences = []
    return m


def test_budget_payload_stored(tmp_path: Path):
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(_mapper_with_budget())
    assert len(client.stored_budgets) == 1
    assert client.stored_budgets[0]["name"] == "Groceries"
    assert len(client.stored_budget_limits) == 1


# Fix 4: --strict flag behaviour
class FailingClient(FakeClient):
    """A FakeClient whose store_transaction always raises FireflyError."""

    def store_transaction(self, payload: dict) -> str:
        raise FireflyError("server error")


def test_strict_false_collects_failure(tmp_path: Path):
    client = FailingClient()
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", strict=False).write(
        _mapper_with_one_txn(), only={"transactions"}
    )
    assert report.counts["transaction"]["failed"] == 1


def test_strict_true_raises_on_failure(tmp_path: Path):
    client = FailingClient()
    with pytest.raises(FireflyError):
        FireflyApiWriter(client, ledger_path=tmp_path / "s.json", strict=True).write(
            _mapper_with_one_txn(), only={"transactions"}
        )


class FailingAccountAndLookupClient(FakeClient):
    """store_account raises FireflyError AND find_account_id also raises (network blip)."""

    def store_account(self, payload: dict) -> str:
        raise FireflyError("create failed")

    def find_account_id(self, name: str) -> str | None:
        raise FireflyError("lookup failed")


def test_account_lookup_failure_in_fallback_is_recorded_not_raised(tmp_path: Path):
    """store_account AND find_account_id both raise → strict=False collects a failure, no escape."""
    m = Mapper()
    m.currencies = [Currency("SEK", "kr", "SEK", 2, True)]
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            Decimal("100"),
            "2010-01-01",
            None,
            "Bank: SEB",
        ),
    ]
    m.transactions = []
    m.budgets = []
    m.recurrences = []

    client = FailingAccountAndLookupClient()
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", strict=False).write(
        m, only={"accounts"}
    )
    assert report.counts["account"]["failed"] == 1


# A5: ledger re-run reads existing plain-text file correctly
def test_ledger_skips_already_seen_external_id(tmp_path: Path):
    """A pre-existing ledger file (plain-text) prevents re-sending."""
    ledger = tmp_path / "state.json"
    ledger.write_text("skrooge:op:1\n")
    client = FakeClient()
    report = FireflyApiWriter(client, ledger_path=ledger).write(
        _mapper_with_one_txn(), only={"transactions"}
    )
    assert len(client.stored_transactions) == 0
    assert report.counts["transaction"]["skipped"] == 1


# Recurrence write-path tests
def _mapper_with_one_recurrence(
    title: str = "Rent",
    repetition_type: str = "monthly",
    amount: Decimal = Decimal("800"),
    currency_code: str = "SEK",
    first_date: str = "2020-06-05",
) -> Mapper:
    m = Mapper()
    m.currencies = [Currency("SEK", "kr", "SEK", 2, True)]
    m.accounts = []
    m.transactions = []
    m.budgets = []
    m.recurrences = [
        Recurrence(
            external_id="skrooge:rec:2",
            title=title,
            kind="withdrawal",
            first_date=first_date,
            repetition_type=repetition_type,
            skip=0,
            amount=amount,
            description=title,
            source_name="Checking",
            destination_name="Landlord",
            currency_code=currency_code,
            category_name=None,
        )
    ]
    return m


def test_recurrence_imported_as_recurring_transaction(tmp_path: Path) -> None:
    """A Recurrence is stored via POST /recurrences with the correct payload."""
    client = FakeClient()
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(
        _mapper_with_one_recurrence(), only={"recurrences"}
    )
    assert len(client.stored_recurrences) == 1
    payload = client.stored_recurrences[0]
    assert payload["type"] == "withdrawal"
    assert payload["title"] == "Rent"
    assert payload["repetitions"][0]["type"] == "monthly"
    assert payload["repetitions"][0]["skip"] == 0
    assert payload["repetitions"][0]["moment"] == "5"
    assert payload["transactions"][0]["amount"] == "800.00"
    assert payload["transactions"][0]["source_name"] == "Checking"
    assert payload["transactions"][0]["destination_name"] == "Landlord"
    assert payload["first_date"] > date.today().isoformat()
    assert report.counts["recurrence"]["created"] == 1


def test_existing_recurrence_skipped_by_title(tmp_path: Path) -> None:
    """When a recurrence with the same title already exists, no store_recurrence call is made."""
    client = FakeClient()
    client.existing_recurrences = {"Rent": "5"}
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(
        _mapper_with_one_recurrence(), only={"recurrences"}
    )
    assert client.stored_recurrences == []
    assert report.counts["recurrence"]["skipped"] == 1


def test_dry_run_skips_recurrence(tmp_path: Path) -> None:
    """dry_run=True makes no writes but previews the recurrence as would-create."""
    client = FakeClient()
    report = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", dry_run=True).write(
        _mapper_with_one_recurrence(), only={"recurrences"}
    )
    assert client.stored_recurrences == []
    assert report.counts["recurrence"]["created"] == 1  # pre-flight: would create


def test_recurrence_skip_passed_through_to_repetition(tmp_path: Path) -> None:
    """rec.skip lands verbatim in repetitions[0].skip (distinct from the date increment)."""
    client = FakeClient()
    m = _mapper_with_one_recurrence()
    m.recurrences[0] = replace(m.recurrences[0], skip=2)  # every 3rd month
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"recurrences"})
    payload = client.stored_recurrences[0]
    assert payload["repetitions"][0]["skip"] == 2
    assert payload["first_date"] > date.today().isoformat()


def test_recurrence_omits_category_name_when_uncategorized(tmp_path: Path) -> None:
    """An uncategorized recurrence must not send category_name (matches the split builder)."""
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(
        _mapper_with_one_recurrence(), only={"recurrences"}
    )
    assert "category_name" not in client.stored_recurrences[0]["transactions"][0]


def test_recurrence_includes_category_name_when_present(tmp_path: Path) -> None:
    """A categorized recurrence sends category_name in its transaction payload."""
    client = FakeClient()
    m = _mapper_with_one_recurrence()
    m.recurrences[0] = replace(m.recurrences[0], category_name="Rent")
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"recurrences"})
    assert client.stored_recurrences[0]["transactions"][0]["category_name"] == "Rent"


def test_update_skips_unchanged_recurrence(tmp_path: Path) -> None:
    """In update mode, an unchanged recurrence is skipped, not PUT."""
    client = FakeClient()
    client.existing_recurrence_content = {
        "Rent": {
            "id": "9",
            "attributes": {
                "type": "withdrawal",
                "transactions": [
                    {
                        "amount": "800.000000",
                        "currency_code": "SEK",
                        "description": "Rent",
                        "source_name": "Checking",
                        "destination_name": "Landlord",
                    }
                ],
                "repetitions": [{"type": "monthly", "moment": "5", "skip": 0}],
            },
        }
    }
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(_mapper_with_one_recurrence(), only={"recurrences"})
    assert client.updated_recurrences == [] and client.stored_recurrences == []
    assert report.counts["recurrence"]["skipped"] == 1


def test_update_skips_unchanged_recurrence_real_field_names(tmp_path: Path) -> None:
    """Firefly's actual GET response uses top-level "type" and "repetitions" (not
    "transaction_type"/"recurrence_repetitions"). An unchanged recurrence must still
    be skipped, not re-PUT, when read from a response shaped like the real API."""
    client = FakeClient()
    client.existing_recurrence_content = {
        "Rent": {
            "id": "9",
            "attributes": {
                "type": "withdrawal",
                "transactions": [
                    {
                        "amount": "800.000000",
                        "currency_code": "SEK",
                        "description": "Rent",
                        "source_name": "Checking",
                        "destination_name": "Landlord",
                    }
                ],
                "repetitions": [{"type": "monthly", "moment": "5", "skip": 0}],
            },
        }
    }
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(_mapper_with_one_recurrence(), only={"recurrences"})
    assert client.updated_recurrences == []
    assert report.counts["recurrence"]["skipped"] == 1


def test_update_rewrites_changed_recurrence(tmp_path: Path) -> None:
    """In update mode, a recurrence with a changed managed field is PUT."""
    client = FakeClient()
    client.existing_recurrence_content = {
        "Rent": {
            "id": "9",
            "attributes": {
                "type": "withdrawal",
                "transactions": [
                    {
                        "amount": "111.00",
                        "currency_code": "SEK",
                        "description": "Rent",
                        "source_name": "Checking",
                        "destination_name": "Landlord",
                    }
                ],
                "repetitions": [{"type": "monthly", "moment": "5", "skip": 0}],
            },
        }
    }
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(_mapper_with_one_recurrence(), only={"recurrences"})
    assert client.updated_recurrences[0][0] == "9"
    assert report.counts["recurrence"]["updated"] == 1


# Bug fix: the ledger short-circuit must not skip change-detection in update mode
def test_update_syncs_budget_limit_even_when_ledger_has_it(tmp_path: Path) -> None:
    """A budget already in the ledger (from a prior run) still gets its new limits synced."""
    ledger = tmp_path / "s.json"
    ledger.write_text("skrooge:budget:Food\n")  # prior run recorded this budget
    client = FakeClient()
    client.existing_budgets = {"Food": "7"}  # budget already on server
    client.existing_budget_limits_raw = [("Food", [])]  # but with no limits yet
    m = Mapper()
    m.budgets = [IRBudget("Food", [BudgetLimit("2020-02-01", "2020-02-28", Decimal("500"))])]
    report = FireflyApiWriter(client, ledger_path=ledger, update=True).write(m, only={"budgets"})
    assert len(client.stored_budget_limits) == 1  # the new limit was posted
    assert report.counts["budget"]["updated"] == 1


def test_update_rewrites_changed_recurrence_even_when_ledger_has_it(tmp_path: Path) -> None:
    """A recurrence already in the ledger (from a prior run) still gets rewritten on change."""
    ledger = tmp_path / "s.json"
    ledger.write_text("skrooge:rec:2\n")  # prior run recorded this recurrence
    client = FakeClient()
    client.existing_recurrence_content = {
        "Rent": {
            "id": "9",
            "attributes": {
                "type": "withdrawal",
                "transactions": [
                    {
                        "amount": "111.00",
                        "currency_code": "SEK",
                        "description": "Rent",
                        "source_name": "Checking",
                        "destination_name": "Landlord",
                    }
                ],
                "repetitions": [{"type": "monthly", "moment": "5", "skip": 0}],
            },
        }
    }
    report = FireflyApiWriter(client, ledger_path=ledger, update=True).write(
        _mapper_with_one_recurrence(), only={"recurrences"}
    )
    assert client.updated_recurrences[0][0] == "9"
    assert report.counts["recurrence"]["updated"] == 1


# ── New coverage tests ──────────────────────────────────────────────────────


def test_account_payload_shape_asset_and_liability(tmp_path: Path) -> None:
    """Asset and liability accounts emit the correct payload keys."""
    m = Mapper()
    m.currencies = [Currency("SEK", "kr", "SEK", 2, True)]
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            Decimal("100"),
            "2010-01-01",
            None,
            "Bank: SEB",
        ),
        Account(
            "skrooge:acct:2",
            "Mortgage",
            "liability",
            None,
            "SEK",
            Decimal("-200"),
            "2010-01-01",
            "loan",
            "",
        ),
    ]
    m.transactions = []
    m.budgets = []
    m.recurrences = []

    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"accounts"})

    assert len(client.stored_accounts) == 2
    asset = client.stored_accounts[0]
    assert asset["type"] == "asset"
    assert asset["account_role"] == "defaultAsset"
    assert asset["currency_code"] == "SEK"
    assert asset["opening_balance"] == "100.00"
    assert asset["opening_balance_date"] == "2010-01-01"
    assert asset["active"] is True

    liability = client.stored_accounts[1]
    assert liability["type"] == "liability"
    assert liability["liability_type"] == "loan"
    # Firefly only respects the sign of `opening_balance` for a liability
    # when liability_direction is "debit"; "credit" silently forces it to
    # abs(value) (confirmed empirically — see commit message).
    assert liability["liability_direction"] == "debit"
    assert liability["opening_balance"] == "-200.00"
    assert "account_role" not in liability


def test_liability_without_opening_balance_uses_credit_direction(tmp_path: Path) -> None:
    """Without an opening balance to apply, the ordinary "credit" direction
    (this account owes the debt) is unaffected by the sign quirk above."""
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:2",
            "Mortgage",
            "liability",
            None,
            "SEK",
            None,
            None,
            "loan",
        ),
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json", assume_empty=True).write(
        m, only={"accounts"}
    )
    assert client.stored_accounts[0]["liability_direction"] == "credit"
    assert "opening_balance" not in client.stored_accounts[0]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """dry_run=True prevents any network call and the ledger file is not created."""
    ledger = tmp_path / "s.json"
    client = FakeClient()
    report = FireflyApiWriter(client, ledger_path=ledger, dry_run=True).write(
        _mapper_with_one_txn()
    )

    # Nothing stored
    assert client.stored_currencies == []
    assert client.stored_accounts == []
    assert client.stored_transactions == []

    # Pre-flight counts what WOULD be created; nothing is written.
    assert report.counts["account"]["created"] >= 1
    assert report.counts["transaction"]["created"] >= 1

    # Ledger file must NOT be written during dry-run
    assert not ledger.exists()


def test_transaction_payload_multisplit_tags_and_foreign(tmp_path: Path) -> None:
    """A multi-split transaction emits group_title; tags and foreign amounts are forwarded."""
    m = Mapper()
    m.currencies = [Currency("SEK", "kr", "SEK", 2, True)]
    m.accounts = []
    m.budgets = []
    m.recurrences = []
    m.transactions = [
        Transaction(
            external_id="skrooge:op:99",
            kind="withdrawal",
            date="2021-03-10",
            splits=[
                Split(
                    Decimal("50"),
                    "SEK",
                    "Checking",
                    "Shop A",
                    tags=("Trip",),
                    foreign_amount=Decimal("90"),
                    foreign_currency_code="EUR",
                ),
                Split(Decimal("50"), "SEK", "Checking", "Shop B"),
            ],
            group_title="Holiday spending",
        )
    ]

    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})

    assert len(client.stored_transactions) == 1
    payload = client.stored_transactions[0]
    # group_title must be present for multi-split
    assert "group_title" in payload
    assert payload["group_title"] == "Holiday spending"
    # First split carries tags and foreign fields
    first = payload["transactions"][0]
    assert first["tags"] == ["Trip"]
    assert first["foreign_amount"] == "90.00"
    assert first["foreign_currency_code"] == "EUR"


def test_budget_write_path_payload_and_failure(tmp_path: Path) -> None:
    """Budget create, idempotency skip, and error-handling (strict / non-strict)."""
    ledger = tmp_path / "s.json"

    def _make_mapper() -> Mapper:
        m = Mapper()
        m.currencies = []
        m.accounts = []
        m.transactions = []
        m.recurrences = []
        m.budgets = [
            IRBudget(
                name="Food",
                limits=[BudgetLimit(start="2020-01-01", end="2020-01-31", amount=Decimal("500"))],
            )
        ]
        return m

    # (a) First run – budget and limit are stored.
    client1 = FakeClient()
    FireflyApiWriter(client1, ledger_path=ledger).write(_make_mapper(), only={"budgets"})
    assert len(client1.stored_budgets) == 1
    assert client1.stored_budgets[0]["name"] == "Food"
    assert len(client1.stored_budget_limits) == 1
    budget_id, limit_payload = client1.stored_budget_limits[0]
    assert limit_payload["start"] == "2020-01-01"
    assert limit_payload["end"] == "2020-01-31"
    assert limit_payload["amount"] == "500.00"

    # (b) Re-run with fresh client – ledger key prevents re-create.
    client2 = FakeClient()
    report2 = FireflyApiWriter(client2, ledger_path=ledger).write(_make_mapper(), only={"budgets"})
    assert len(client2.stored_budgets) == 0
    assert report2.counts["budget"]["skipped"] >= 1

    # (c) Error handling: FailingBudgetClient with strict=False collects failure.
    class FailingBudgetClient(FakeClient):
        def store_budget(self, payload: dict) -> str:
            raise FireflyError("boom")

    ledger_fail = tmp_path / "fail.json"
    client_fail = FailingBudgetClient()
    report_fail = FireflyApiWriter(client_fail, ledger_path=ledger_fail, strict=False).write(
        _make_mapper(), only={"budgets"}
    )
    assert report_fail.counts["budget"]["failed"] == 1

    # strict=True must re-raise.
    with pytest.raises(FireflyError):
        FireflyApiWriter(
            FailingBudgetClient(), ledger_path=tmp_path / "fail2.json", strict=True
        ).write(_make_mapper(), only={"budgets"})


def test_existing_account_is_reused_not_recreated(tmp_path):
    client = FakeClient()
    client.existing_accounts = {"Checking": "99"}  # already in Firefly
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json")
    report = writer.write(_mapper_with_one_txn(), only={"accounts"})
    assert client.stored_accounts == []  # never POSTed
    assert report.counts["account"]["skipped"] == 1
    assert report.counts["account"]["created"] == 0
    assert writer._account_ids["Checking"] == "99"  # id recorded for later reuse


def test_existing_transaction_skipped_by_external_id(tmp_path):
    client = FakeClient()
    client.existing_txn_ids = {"skrooge:op:1"}  # already imported
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json")
    report = writer.write(_mapper_with_one_txn(), only={"transactions"})
    assert client.stored_transactions == []  # never POSTed
    assert report.counts["transaction"]["skipped"] == 1


def test_existing_budget_reused_by_name(tmp_path):
    from skrooge2firefly.model.entities import BudgetLimit, IRBudget

    client = FakeClient()
    client.existing_budgets = {"Food": "12"}
    m = Mapper()
    m.budgets = [
        IRBudget(name="Food", limits=[BudgetLimit("2020-01-01", "2020-01-31", Decimal("500"))])
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json")
    report = writer.write(m, only={"budgets"})
    assert client.stored_budgets == []  # not recreated
    assert client.stored_budget_limits == []  # limits untouched
    assert report.counts["budget"]["skipped"] == 1


def test_update_adds_missing_budget_limit(tmp_path):
    client = FakeClient()
    client.existing_budgets = {"Food": "7"}
    client.existing_budget_limits_raw = [("Food", [])]  # budget exists, no limits yet
    m = Mapper()
    m.budgets = [
        IRBudget(name="Food", limits=[BudgetLimit("2020-01-01", "2020-01-31", Decimal("500"))])
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(m, only={"budgets"})
    assert len(client.stored_budget_limits) == 1
    assert report.counts["budget"]["updated"] == 1


def test_assume_empty_skips_reconciliation(tmp_path):
    class CountingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.reconcile_calls = 0

        def account_index(self):
            self.reconcile_calls += 1
            return {}

    client = CountingClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", assume_empty=True)
    writer.write(_mapper_with_one_txn(), only={"accounts"})
    assert client.reconcile_calls == 0  # no reconciliation reads issued


class StatefulClient(FakeClient):
    """A FakeClient whose stored objects feed its reconciliation reads."""

    def account_index(self):
        return {p["name"]: str(i) for i, p in enumerate(self.stored_accounts, 1)}

    def budget_index(self):
        return {p["name"]: str(i) for i, p in enumerate(self.stored_budgets, 1)}

    def existing_external_ids(self, prefix: str = "skrooge:"):
        ids = set()
        for payload in self.stored_transactions:
            for split in payload["transactions"]:
                ids.add(split["external_id"])
        return ids


def test_second_run_is_full_noop(tmp_path):
    client = StatefulClient()
    m = _mapper_with_one_txn()
    first = FireflyApiWriter(client, ledger_path=tmp_path / "a.json").write(
        m, only={"accounts", "transactions"}
    )
    assert first.counts["account"]["created"] == 1
    assert first.counts["transaction"]["created"] == 1

    # Fresh ledger (simulating a different machine) — reconciliation alone must prevent dupes.
    second = FireflyApiWriter(client, ledger_path=tmp_path / "b.json").write(
        m, only={"accounts", "transactions"}
    )
    assert second.counts["account"]["created"] == 0
    assert second.counts["transaction"]["created"] == 0
    assert second.counts["account"]["skipped"] == 1
    assert second.counts["transaction"]["skipped"] == 1
    assert len(client.stored_accounts) == 1  # still only one
    assert len(client.stored_transactions) == 1


# ── Concurrency tests ────────────────────────────────────────────────────────


def _mapper_with_n_txns(n: int) -> Mapper:
    m = Mapper()
    m.transactions = [
        Transaction(
            f"skrooge:op:{i}",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("1.00"), "SEK", "Checking", "ICA")],
        )
        for i in range(n)
    ]
    return m


def test_concurrent_import_posts_all_transactions(tmp_path: Path) -> None:
    """With concurrency=4, all 10 transactions are POSTed and recorded in the ledger."""
    ledger = tmp_path / "state.txt"
    client = FakeClient()
    report = FireflyApiWriter(client, ledger_path=ledger, concurrency=4).write(
        _mapper_with_n_txns(10), only={"transactions"}
    )
    assert len(client.stored_transactions) == 10
    assert report.counts["transaction"]["created"] == 10
    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(lines) == 10


def test_multisplit_without_comment_still_gets_group_title(tmp_path: Path) -> None:
    """Multi-split group with no group_title must still receive a non-empty group_title."""
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:99",
            "withdrawal",
            "2020-05-01",
            [
                Split(Decimal("10.00"), "SEK", "Checking", "ICA", category_name="Food"),
                Split(Decimal("5.00"), "SEK", "Checking", "ICA", category_name="Other"),
            ],
            group_title=None,
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    payload = client.stored_transactions[0]
    assert payload.get("group_title")  # present and non-empty
    assert len(payload["group_title"]) > 0


def test_single_split_has_no_group_title(tmp_path: Path) -> None:
    """Single-split transactions must NOT receive a group_title key."""
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(
        _mapper_with_one_txn(), only={"transactions"}
    )
    assert "group_title" not in client.stored_transactions[0]


def test_account_payload_includes_active_flag(tmp_path):
    from skrooge2firefly.model.entities import Account

    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:9",
            "Archived",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            "",
            active=False,
        ),
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"accounts"})
    # Created active so transactions can reference it; closed state applied at the end.
    assert client.stored_accounts[0]["active"] is True
    assert client.updated_accounts[-1][1]["active"] is False


def test_concurrent_import_counts_failures(tmp_path: Path) -> None:
    """With concurrency=4 and strict=False, all failures are collected without exception."""

    class AlwaysFailingClient(FakeClient):
        def store_transaction(self, payload: dict) -> str:
            raise FireflyError("boom")

    client = AlwaysFailingClient()
    report = FireflyApiWriter(
        client, ledger_path=tmp_path / "state.txt", concurrency=4, strict=False
    ).write(_mapper_with_n_txns(10), only={"transactions"})
    assert report.counts["transaction"]["failed"] == 10


# ── Description fallback tests ────────────────────────────────────────────────


def test_description_falls_back_to_payee(tmp_path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("12.50"), "SEK", "Checking", "ICA")],  # no notes
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    assert client.stored_transactions[0]["transactions"][0]["description"] == "ICA"


def test_deposit_description_falls_back_to_source_payee(tmp_path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:2",
            "deposit",
            "2020-05-01",
            [Split(Decimal("100.00"), "SEK", "Employer", "Checking")],  # no notes
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    assert client.stored_transactions[0]["transactions"][0]["description"] == "Employer"


def test_transfer_description_fallback(tmp_path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:grp:1",
            "transfer",
            "2020-05-01",
            [Split(Decimal("500.00"), "SEK", "Checking", "Savings")],  # no notes
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    desc = client.stored_transactions[0]["transactions"][0]["description"]
    assert desc == "Transfer: Checking → Savings"


def test_description_prefers_notes_when_present(tmp_path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:3",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("9.00"), "SEK", "Checking", "ICA", notes="Lunch")],
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    assert client.stored_transactions[0]["transactions"][0]["description"] == "Lunch"


def test_update_mode_resyncs_existing_transaction(tmp_path):
    client = FakeClient()
    m = _mapper_with_one_txn()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = writer._update_payload(m.transactions[0])["transactions"][0]
    changed_split = dict(split)
    changed_split["description"] = "OLD"  # differs from the mapper's real content
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [changed_split]}}
    report = writer.write(m, only={"transactions"})
    assert client.stored_transactions == []
    assert client.updated_transactions[0][0] == "500"
    assert report.counts["transaction"]["updated"] == 1


def test_update_skips_unchanged_transaction(tmp_path):
    client = FakeClient()
    m = _mapper_with_one_txn()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = writer._update_payload(m.transactions[0])["transactions"][0]  # exact managed fields
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [dict(split)]}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions == []
    assert report.counts["transaction"]["skipped"] == 1


def test_update_skips_when_only_amount_precision_and_date_tz_differ(tmp_path):
    """Normalized compare: 6dp amount + TZ datetime from Firefly is NOT a change."""
    client = FakeClient()
    m = _mapper_with_one_txn()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = dict(writer._update_payload(m.transactions[0])["transactions"][0])
    # Firefly returns amounts at 6dp and dates as TZ datetimes; same value, different form.
    split["amount"] = str(Decimal(split["amount"])) + "0000"  # e.g. "12.50" -> "12.500000"
    split["date"] = split["date"] + "T00:00:00+02:00"
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [split]}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions == []  # normalized -> no PUT
    assert report.counts["transaction"]["skipped"] == 1


def test_update_rewrites_changed_transaction(tmp_path):
    client = FakeClient()
    m = _mapper_with_one_txn()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = writer._update_payload(m.transactions[0])["transactions"][0]
    changed_split = dict(split)
    changed_split["amount"] = "999.00"  # a real value change, not just formatting
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [changed_split]}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions[0][0] == "500"
    assert report.counts["transaction"]["updated"] == 1


def test_update_mode_creates_when_absent(tmp_path):
    client = FakeClient()
    client.existing_group_ids = {}
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(_mapper_with_one_txn(), only={"transactions"})
    assert len(client.stored_transactions) == 1
    assert report.counts["transaction"]["created"] == 1


def test_update_detects_changed_tags(tmp_path):
    """A tags-only difference must be seen as a change (tags weren't compared before)."""
    client = FakeClient()
    m = _mapper_with_one_txn()
    m.transactions[0] = Transaction(
        m.transactions[0].external_id,
        m.transactions[0].kind,
        m.transactions[0].date,
        [
            Split(
                Decimal("12.50"),
                "SEK",
                "Checking",
                "ICA",
                category_name="Food",
                tags=("Trip",),
            )
        ],
    )
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = dict(writer._update_payload(m.transactions[0])["transactions"][0])
    split["tags"] = ["Other"]  # differs from the mapper's "Trip"
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [split]}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions[0][0] == "500"
    assert report.counts["transaction"]["updated"] == 1


def test_update_detects_changed_foreign_amount(tmp_path):
    """A foreign_amount-only difference must be seen as a change."""
    client = FakeClient()
    m = _mapper_with_one_txn()
    m.transactions[0] = Transaction(
        m.transactions[0].external_id,
        m.transactions[0].kind,
        m.transactions[0].date,
        [
            Split(
                Decimal("12.50"),
                "SEK",
                "Checking",
                "ICA",
                category_name="Food",
                foreign_amount=Decimal("1.50"),
                foreign_currency_code="EUR",
            )
        ],
    )
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    split = dict(writer._update_payload(m.transactions[0])["transactions"][0])
    split["foreign_amount"] = "9.99"  # differs from the mapper's 1.50
    client.existing_group_ids = {"skrooge:op:1": "500"}
    client.existing_txn_content = {"skrooge:op:1": {"group_id": "500", "splits": [split]}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions[0][0] == "500"
    assert report.counts["transaction"]["updated"] == 1


def test_update_split_reorder_is_not_a_change(tmp_path):
    """A multi-split transaction whose existing splits are in a different order,
    but otherwise identical, must not be reported as changed (order-independent compare)."""
    client = FakeClient()
    m = Mapper()
    m.transactions = [
        Transaction(
            external_id="skrooge:op:99",
            kind="withdrawal",
            date="2021-03-10",
            splits=[
                Split(Decimal("50"), "SEK", "Checking", "Shop A", category_name="Food"),
                Split(Decimal("30"), "SEK", "Checking", "Shop B", category_name="Fun"),
            ],
            group_title="Holiday spending",
        )
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    splits = writer._update_payload(m.transactions[0])["transactions"]
    reversed_splits = [dict(splits[1]), dict(splits[0])]
    client.existing_group_ids = {"skrooge:op:99": "500"}
    client.existing_txn_content = {"skrooge:op:99": {"group_id": "500", "splits": reversed_splits}}
    report = writer.write(m, only={"transactions"})
    assert client.updated_transactions == []
    assert report.counts["transaction"]["skipped"] == 1


def test_update_mode_mirrors_account_active(tmp_path):
    from skrooge2firefly.model.entities import Account

    client = FakeClient()
    client.account_state_map = {"Archived": ("7", True)}
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Archived",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            "",
            active=False,
        ),
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(m, only={"accounts"})
    assert client.updated_accounts[0][0] == "7"
    assert client.updated_accounts[0][1]["active"] is False
    assert report.counts["account"]["updated"] == 1


def test_update_mode_refreshes_active_state_after_close(tmp_path):
    """After PUTting active=False, the writer's live tracking must reflect it
    right away — not only at end-of-run via _restore_account_states — or
    _activate_accounts_for_writes reads the stale value and wrongly skips
    reactivating the account for same-run transaction writes."""
    from skrooge2firefly.model.entities import Account
    from skrooge2firefly.writers.base import WriteReport

    client = FakeClient()
    client.account_state_map = {"Archived": ("7", True)}
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Archived",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            "",
            active=False,
        ),
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    writer._reconcile()
    writer._write_accounts(m, WriteReport())
    assert client.updated_accounts[0][1]["active"] is False
    assert writer._account_active["Archived"] is False


def test_update_mode_no_account_patch_when_active_matches(tmp_path):
    from skrooge2firefly.model.entities import Account

    client = FakeClient()
    client.account_state_map = {"Open": ("8", True)}
    client.existing_account_attrs = {
        "Open": {
            "id": "8",
            "attributes": {
                "name": "Open",
                "currency_code": "SEK",
                "notes": None,
                "account_role": "defaultAsset",
                "active": True,
            },
        }
    }
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:2",
            "Open",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            "",
            active=True,
        ),
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    writer.write(m, only={"accounts"})
    assert client.updated_accounts == []


def test_update_patches_changed_account_notes(tmp_path):
    from skrooge2firefly.model.entities import Account

    client = FakeClient()
    client.account_state_map = {"Checking": ("1", True)}
    client.existing_account_attrs = {
        "Checking": {
            "id": "1",
            "attributes": {
                "name": "Checking",
                "currency_code": "SEK",
                "notes": "OLD",
                "active": True,
            },
        }
    }
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            None,
            "SEK",
            None,
            None,
            None,
            "NEW",
            active=True,
        ),
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(m, only={"accounts"})
    assert client.updated_accounts  # a PUT happened
    assert report.counts["account"]["updated"] >= 1


def test_account_update_does_not_resend_unchanged_opening_balance(tmp_path):
    """A notes-only change must not re-send opening_balance/opening_balance_date,
    or it would revert a manual Firefly correction to the opening balance."""
    client = FakeClient()
    client.account_state_map = {"Checking": ("1", True)}
    client.existing_account_attrs = {
        "Checking": {
            "id": "1",
            "attributes": {
                "name": "Checking",
                "currency_code": "SEK",
                "notes": "OLD",
                "active": True,
                "opening_balance": "100.00",
                "opening_balance_date": "2010-01-01",
            },
        }
    }
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            None,
            "SEK",
            Decimal("100"),
            "2010-01-01",
            None,
            "NEW",
            active=True,
        ),
    ]
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(m, only={"accounts"})
    assert client.updated_accounts  # the notes change triggers a PUT
    payload = client.updated_accounts[0][1]
    assert "opening_balance" not in payload
    assert "opening_balance_date" not in payload
    assert report.counts["account"]["updated"] == 1


def test_account_update_syncs_changed_opening_balance(tmp_path):
    """When the opening balance itself changed, it must be included and trigger a sync."""
    client = FakeClient()
    client.account_state_map = {"Checking": ("1", True)}
    client.existing_account_attrs = {
        "Checking": {
            "id": "1",
            "attributes": {
                "name": "Checking",
                "currency_code": "SEK",
                "notes": "Bank: SEB",
                "active": True,
                "opening_balance": "50.00",
                "opening_balance_date": "2010-01-01",
            },
        }
    }
    m = _mapper_with_one_txn()  # Checking has opening_balance=100, notes="Bank: SEB" (unchanged)
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(m, only={"accounts"})
    payload = client.updated_accounts[0][1]
    assert payload["opening_balance"] == "100.00"
    assert report.counts["account"]["updated"] == 1


def test_description_combines_payee_and_category(tmp_path):
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:7",
            "withdrawal",
            "2026-05-01",
            [
                Split(
                    Decimal("9.00"),
                    "DKK",
                    "Oliv - Revolut DKK",
                    "Winston Craft",
                    category_name="Food > Cafes & Bar",
                )
            ],  # no notes
        )
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json").write(m, only={"transactions"})
    assert (
        client.stored_transactions[0]["transactions"][0]["description"]
        == "Winston Craft Cafes & Bar"
    )


def test_consecutive_failures_trip_circuit_breaker(tmp_path: Path):
    """A dead server must abort the run, not burn through the whole queue."""

    class DeadClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def store_transaction(self, payload: dict) -> str:
            self.attempts += 1
            raise FireflyError("POST transactions failed (502): gateway")

    m = Mapper()
    m.currencies = []
    m.transactions = [
        Transaction(
            f"skrooge:op:{i}",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("1.00"), "SEK", "Checking", "ICA")],
        )
        for i in range(30)
    ]
    client = DeadClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "ledger.txt", assume_empty=True)
    with pytest.raises(FireflyError, match="consecutive"):
        writer.write(m, only={"transactions"})
    assert client.attempts == 15  # aborted, not 30


def test_breaker_resets_on_success(tmp_path: Path):
    """Interleaved successes keep the breaker from tripping."""

    class FlakyClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def store_transaction(self, payload: dict) -> str:
            self.attempts += 1
            if self.attempts % 2:
                raise FireflyError("POST transactions failed (502): gateway")
            return str(self.attempts)

    m = Mapper()
    m.currencies = []
    m.transactions = [
        Transaction(
            f"skrooge:op:{i}",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("1.00"), "SEK", "Checking", "ICA")],
        )
        for i in range(30)
    ]
    client = FlakyClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "ledger.txt", assume_empty=True)
    report = writer.write(m, only={"transactions"})
    assert client.attempts == 30
    assert report.counts["transaction"]["created"] == 15


def test_inactive_accounts_activated_for_writes_then_restored(tmp_path: Path):
    """Firefly won't resolve inactive accounts as transaction counterparties, so
    the writer must activate them for the duration of the import and restore
    the closed state at the end."""

    events: list[tuple] = []

    class SequencedClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.existing_accounts = {"Checking": "10", "Old Loan": "77"}
            self.account_state_map = {"Checking": ("10", True), "Old Loan": ("77", False)}

        def update_account(self, account_id: str, payload: dict) -> None:
            events.append(("update", account_id, payload["active"]))
            super().update_account(account_id, payload)

        def store_transaction(self, payload: dict) -> str:
            events.append(("txn", payload["transactions"][0]["destination_name"]))
            return super().store_transaction(payload)

    m = Mapper()
    m.currencies = []
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            active=True,
        ),
        Account(
            "skrooge:acct:2",
            "Old Loan",
            "asset",
            "defaultAsset",
            "SEK",
            None,
            None,
            None,
            active=False,
        ),
    ]
    m.transactions = [
        Transaction(
            "skrooge:grp:9",
            "transfer",
            "2020-01-01",
            [Split(Decimal("100"), "SEK", "Checking", "Old Loan")],
        ),
    ]
    client = SequencedClient()
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "l.txt")
    report = writer.write(m, only={"accounts", "transactions"})
    assert not report.has_failures
    assert events == [
        ("update", "77", True),  # activated before any transaction
        ("txn", "Old Loan"),
        ("update", "77", False),  # closed state restored at the end
    ]


def test_split_amounts_use_currency_decimal_places(tmp_path: Path):
    """BTC has 6 decimal places; formatting at 2 destroys sub-cent amounts."""
    from skrooge2firefly.model.entities import Currency

    m = Mapper()
    m.currencies = [Currency("BTC", "Bitcoin", "BTC", 6, False)]
    m.transactions = [
        Transaction(
            "skrooge:op:9",
            "withdrawal",
            "2021-01-01",
            [Split(Decimal("0.002345"), "BTC", "Wallet", "Exchange")],
        ),
    ]
    client = FakeClient()
    client.existing_currencies = {"BTC"}
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "l.txt", assume_empty=True)
    writer.write(m, only={"transactions"})
    assert client.stored_transactions[0]["transactions"][0]["amount"] == "0.002345"


def test_cc_asset_account_payload_includes_credit_card_fields(tmp_path: Path):
    """Firefly requires credit_card_type + monthly_payment_date for ccAsset roles."""
    m = Mapper()
    m.accounts = [
        Account("skrooge:acct:9", "Norwegian CC", "asset", "ccAsset", "SEK", None, None, None),
    ]
    client = FakeClient()
    FireflyApiWriter(client, ledger_path=tmp_path / "s.json", assume_empty=True).write(
        m, only={"accounts"}
    )
    payload = client.stored_accounts[0]
    assert payload["account_role"] == "ccAsset"
    assert payload["credit_card_type"] == "monthlyFull"
    assert payload["monthly_payment_date"] == "2020-01-01"


def test_writer_prefers_server_currency_decimals(tmp_path: Path):
    """Skrooge says SEK has 6 decimals but Firefly stores 2; the server wins,
    otherwise Firefly re-rounds our 6dp strings with a different rounding mode."""
    from skrooge2firefly.model.entities import Currency

    class DecimalsClient(FakeClient):
        def currency_decimals(self) -> dict:
            return {"SEK": 2}

    m = Mapper()
    m.currencies = [Currency("SEK", "Swedish krona", "kr", 6, True)]  # Skrooge's 6dp
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2021-01-01",
            [Split(Decimal("715.365"), "SEK", "Checking", "Shop")],
        ),
    ]
    client = DecimalsClient()
    client.existing_currencies = {"SEK"}
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "l.txt", assume_empty=True)
    writer.write(m, only={"transactions"})
    assert client.stored_transactions[0]["transactions"][0]["amount"] == "715.36"


def test_transaction_issue_detects_bad_payloads():
    from skrooge2firefly.writers.firefly_api import _transaction_issue

    assert _transaction_issue({"transactions": []}) == "no splits"
    ok = {"transactions": [{"amount": "12.50", "source_name": "A", "destination_name": "B"}]}
    assert _transaction_issue(ok) is None
    zero = {"transactions": [{"amount": "0.00", "source_name": "A", "destination_name": "B"}]}
    assert "not greater than 0" in _transaction_issue(zero)
    noacct = {"transactions": [{"amount": "5", "source_name": "", "destination_name": "B"}]}
    assert "source or destination" in _transaction_issue(noacct)


def test_dry_run_flags_zero_amount_transaction(tmp_path: Path):
    """Pre-flight must catch amounts that would be rejected (the 'amount 0'
    class of silent failure) before anything is written."""
    m = Mapper()
    m.transactions = [
        Transaction(
            "skrooge:op:1", "withdrawal", "2020-01-01", [Split(Decimal("0"), "SEK", "A", "B")]
        ),
    ]
    client = FakeClient()
    report = FireflyApiWriter(
        client, ledger_path=tmp_path / "l.txt", dry_run=True, assume_empty=True
    ).write(m, only={"transactions"})
    assert client.stored_transactions == []  # nothing written
    assert report.counts["transaction"]["failed"] == 1
    assert any("not greater than 0" in msg for _, msg in report.errors)


def test_dry_run_reconciles_and_marks_existing_as_skipped(tmp_path: Path):
    """A transaction already on the instance is previewed as skipped, a new one
    as would-create — so the pre-flight count is accurate."""
    m = _mapper_with_one_txn()
    client = FakeClient()
    client.existing_txn_ids = {"skrooge:op:1"}  # pretend it's already imported
    report = FireflyApiWriter(client, ledger_path=tmp_path / "l.txt", dry_run=True).write(
        m, only={"transactions"}
    )
    assert report.counts["transaction"]["skipped"] == 1
    assert report.counts["transaction"]["created"] == 0


def test_next_first_date_monthly_rolls_past_anchor_forward_preserving_day():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    # anchor in the past, every month; next occurrence after 2026-07-07 is the 15th
    assert _next_first_date("2020-03-15", "monthly", 1, date(2026, 7, 7)) == "2026-07-15"


def test_next_first_date_already_future_is_unchanged():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    assert _next_first_date("2026-09-01", "monthly", 1, date(2026, 7, 7)) == "2026-09-01"


def test_next_first_date_every_two_months_keeps_phase():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    # every 2 months from Jan 2020 on the 10th -> odd months; next after 2026-07-07
    assert _next_first_date("2020-01-10", "monthly", 2, date(2026, 7, 7)) == "2026-07-10"


def test_next_first_date_month_end_clamps_short_month():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    # 31st, monthly; February target clamps to 28/29
    assert _next_first_date("2020-01-31", "monthly", 1, date(2026, 1, 31)) == "2026-02-28"


def test_next_first_date_weekly_and_daily():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    assert _next_first_date("2020-01-01", "weekly", 1, date(2026, 7, 7)) == "2026-07-08"
    assert _next_first_date("2026-07-06", "daily", 1, date(2026, 7, 7)) == "2026-07-08"


def test_next_first_date_yearly():
    from skrooge2firefly.writers.firefly_api import _next_first_date

    assert _next_first_date("2018-04-30", "yearly", 1, date(2026, 7, 7)) == "2027-04-30"


def test_orphan_transaction_deleted_when_decider_says_delete(tmp_path: Path) -> None:
    """An update-mode transaction absent from the newer file is deleted when told to."""
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        orphan_decider=FixedDecider("delete"),
    )
    report = writer.write(Mapper(), only={"transactions"})  # empty file -> op:99 is orphan
    assert client.deleted_transactions == ["900"]
    assert report.counts["orphan-transaction"]["deleted"] == 1


def test_orphan_transaction_delete_discards_from_ledger(tmp_path: Path) -> None:
    """Deleting an orphan transaction also forgets it in the idempotency ledger."""
    from skrooge2firefly.writers.firefly_api import _Ledger
    from skrooge2firefly.writers.orphans import FixedDecider

    ledger_path = tmp_path / "s.json"
    ledger_path.write_text("skrooge:op:99\n")  # prior run recorded it
    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    writer = FireflyApiWriter(
        client,
        ledger_path=ledger_path,
        update=True,
        orphan_decider=FixedDecider("delete"),
    )
    writer.write(Mapper(), only={"transactions"})
    assert client.deleted_transactions == ["900"]
    assert _Ledger(ledger_path).has("skrooge:op:99") is False


def test_orphan_transaction_ignored_by_default(tmp_path: Path) -> None:
    """Without an explicit decider, orphan transactions are reported but never deleted."""
    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    writer = FireflyApiWriter(client, ledger_path=tmp_path / "s.json", update=True)
    report = writer.write(Mapper(), only={"transactions"})
    assert client.deleted_transactions == []
    assert report.counts["orphan-transaction"]["skipped"] == 1


def test_orphans_not_deleted_when_resolve_orphans_false(tmp_path):
    """A date-filtered run must not treat out-of-window server txns as orphans."""
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "old history"}],
        }
    }
    # mapper has NO transactions (simulating all filtered out of the window)
    FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        orphan_decider=FixedDecider("delete"),
        resolve_orphans=False,
    ).write(Mapper(), only={"transactions"})
    assert client.deleted_transactions == []  # nothing deleted despite delete decider


def test_orphan_transaction_dry_run_previews_without_deleting(tmp_path: Path) -> None:
    """dry_run reports the intended orphan action but performs no delete."""
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        dry_run=True,
        orphan_decider=FixedDecider("delete"),
    )
    report = writer.write(Mapper(), only={"transactions"})
    assert client.deleted_transactions == []
    assert report.counts["orphan-transaction"]["deleted"] == 1


def test_orphan_recurrence_deleted_when_decider_says_delete(tmp_path: Path) -> None:
    """A recurrence bearing the Skrooge marker and absent from the file is deleted."""
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_recurrence_content = {
        "Old Rent": {
            "id": "42",
            "attributes": {"notes": "Imported from Skrooge recurring operation."},
        }
    }
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        orphan_decider=FixedDecider("delete"),
    )
    report = writer.write(Mapper(), only={"recurrences"})
    assert client.deleted_recurrences == ["42"]
    assert report.counts["orphan-recurrence"]["deleted"] == 1


def test_orphan_recurrence_without_marker_is_never_flagged(tmp_path: Path) -> None:
    """A recurrence without the Skrooge marker is untouched (not our data to delete)."""
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_recurrence_content = {
        "Hand-made rule": {"id": "42", "attributes": {"notes": "set up manually"}}
    }
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        orphan_decider=FixedDecider("delete"),
    )
    report = writer.write(Mapper(), only={"recurrences"})
    assert client.deleted_recurrences == []
    assert report.counts["orphan-recurrence"]["deleted"] == 0


def test_dry_run_writes_decisions_file_with_orphan_choices(tmp_path: Path) -> None:
    """A dry run with decisions_path records this run's orphan decisions to disk."""
    from skrooge2firefly.writers import decisions
    from skrooge2firefly.writers.orphans import FixedDecider

    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    decisions_path = tmp_path / "decisions.json"
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        dry_run=True,
        orphan_decider=FixedDecider("delete"),
        decisions_path=decisions_path,
    )
    writer.write(Mapper(), only={"transactions"})
    assert decisions.load(decisions_path) == {"skrooge:op:99": "delete"}


def test_partial_dry_runs_merge_into_decisions_file(tmp_path: Path) -> None:
    """A second partial dry run (different --only section) preserves the first's choices."""
    from skrooge2firefly.writers import decisions
    from skrooge2firefly.writers.orphans import FixedDecider

    decisions_path = tmp_path / "decisions.json"

    # Run A: dry-run over transactions only -> resolves skrooge:op:99
    client_a = FakeClient()
    client_a.existing_group_ids = {"skrooge:op:99": "900"}
    client_a.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    writer_a = FireflyApiWriter(
        client_a,
        ledger_path=tmp_path / "s.json",
        update=True,
        dry_run=True,
        orphan_decider=FixedDecider("delete"),
        decisions_path=decisions_path,
    )
    writer_a.write(Mapper(), only={"transactions"})

    # Run B: separate dry-run over recurrences only -> resolves a different orphan
    client_b = FakeClient()
    client_b.existing_recurrence_content = {
        "Netflix": {
            "id": "42",
            "attributes": {"notes": "Imported from Skrooge recurring operation."},
        }
    }
    writer_b = FireflyApiWriter(
        client_b,
        ledger_path=tmp_path / "s2.json",
        update=True,
        dry_run=True,
        orphan_decider=FixedDecider("delete"),
        decisions_path=decisions_path,
    )
    writer_b.write(Mapper(), only={"recurrences"})

    both = decisions.load(decisions_path)
    assert "skrooge:op:99" in both
    assert "rec:Netflix" in both


def test_real_run_applies_decisions_file_without_prompting(tmp_path: Path) -> None:
    """A real run consults decisions_path first, never falling back to the base decider."""
    from skrooge2firefly.writers import decisions

    class _ExplodingDecider:
        def decide(self, kind: str, key: str, label: str) -> str:
            raise AssertionError("should never be consulted: decisions file covers this key")

    client = FakeClient()
    client.existing_group_ids = {"skrooge:op:99": "900"}
    client.existing_txn_content = {
        "skrooge:op:99": {
            "group_id": "900",
            "splits": [{"external_id": "skrooge:op:99", "description": "gone"}],
        }
    }
    decisions_path = tmp_path / "decisions.json"
    decisions.save(decisions_path, {"skrooge:op:99": "delete"})
    writer = FireflyApiWriter(
        client,
        ledger_path=tmp_path / "s.json",
        update=True,
        orphan_decider=_ExplodingDecider(),
        decisions_path=decisions_path,
    )
    report = writer.write(Mapper(), only={"transactions"})
    assert client.deleted_transactions == ["900"]
    assert report.counts["orphan-transaction"]["deleted"] == 1


def test_moment_by_period_type():
    from skrooge2firefly.writers.firefly_api import _moment

    assert _moment("2026-07-15", "daily") == ""
    assert _moment("2026-07-15", "weekly") == "3"  # Wednesday
    assert _moment("2026-07-15", "monthly") == "15"
    assert _moment("2026-07-15", "yearly") == "2026-07-15"
