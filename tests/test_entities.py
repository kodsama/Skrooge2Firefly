from decimal import Decimal

from skrooge2firefly.model.entities import (
    Account,
    BudgetLimit,
    Currency,
    IRBudget,
    Split,
    Transaction,
)


def test_entities_construct_and_are_frozen():
    cur = Currency(code="SEK", name="Swedish kronor", symbol="SEK", decimal_places=2, primary=True)
    acct = Account(
        external_id="skrooge:acct:1",
        name="Checking",
        kind="asset",
        role="defaultAsset",
        currency_code="SEK",
        opening_balance=Decimal("100.00"),
        opening_balance_date="2010-01-01",
        liability_type=None,
        notes="Bank: SEB",
    )
    split = Split(
        amount=Decimal("12.50"),
        currency_code="SEK",
        category_name="Food > Groceries",
        source_name="Checking",
        destination_name="ICA",
        reconciled=True,
    )
    txn = Transaction(
        external_id="skrooge:op:5",
        kind="withdrawal",
        date="2020-05-01",
        splits=[split],
    )
    budget = IRBudget(
        name="Food",
        limits=[BudgetLimit(start="2020-01-01", end="2020-01-31", amount=Decimal("500"))],
    )

    assert cur.primary is True
    assert acct.kind == "asset"
    assert txn.splits[0].destination_name == "ICA"
    assert budget.limits[0].amount == Decimal("500")
