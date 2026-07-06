from decimal import Decimal

from skrooge2firefly.export.puller import pull


class StubClient:
    def list_accounts(self, account_type):
        if account_type == "asset":
            return [
                {
                    "id": "1",
                    "attributes": {
                        "name": "Checking",
                        "type": "asset",
                        "account_role": "defaultAsset",
                        "currency_code": "SEK",
                        "notes": "Bank: SEB",
                        "active": True,
                        "opening_balance": "100.00",
                        "opening_balance_date": "2010-01-01",
                    },
                }
            ]
        return [
            {
                "id": "2",
                "attributes": {
                    "name": "Mortgage",
                    "type": "loan",
                    "account_role": None,
                    "currency_code": "SEK",
                    "notes": None,
                    "active": False,
                    "opening_balance": "0",
                    "opening_balance_date": None,
                },
            }
        ]

    def list_transaction_groups(self):
        return [
            {
                "id": "10",
                "attributes": {
                    "group_title": None,
                    "transactions": [
                        {
                            "type": "withdrawal",
                            "date": "2020-05-01T00:00:00+02:00",
                            "amount": "12.500000000000",
                            "currency_code": "SEK",
                            "foreign_amount": None,
                            "foreign_currency_code": None,
                            "description": "ICA Food",
                            "source_name": "Checking",
                            "destination_name": "ICA",
                            "category_name": "Food > Groceries",
                            "tags": ["a"],
                            "notes": "memo",
                            "reconciled": True,
                            "external_id": "skrooge:op:7",
                        }
                    ],
                },
            },
            {
                "id": "11",
                "attributes": {
                    "group_title": None,
                    "transactions": [
                        {
                            "type": "opening balance",
                            "date": "2010-01-01T00:00:00+02:00",
                            "amount": "100",
                            "currency_code": "SEK",
                            "foreign_amount": None,
                            "foreign_currency_code": None,
                            "description": "Opening balance",
                            "source_name": "Opening balance account",
                            "destination_name": "Checking",
                            "category_name": None,
                            "tags": [],
                            "notes": None,
                            "reconciled": False,
                            "external_id": None,
                        }
                    ],
                },
            },
        ]

    def list_budgets_with_limits(self):
        return [
            (
                "Food",
                [{"attributes": {"start": "2026-01-01", "end": "2026-01-31", "amount": "500"}}],
            )
        ]


def test_pull_maps_accounts_transactions_budgets():
    data = pull(StubClient())
    assert [a.name for a in data.accounts] == ["Checking", "Mortgage"]
    checking, mortgage = data.accounts
    assert checking.kind == "asset" and checking.opening_balance == Decimal("100.00")
    assert mortgage.kind == "liability" and mortgage.liability_type == "loan"
    assert not mortgage.active
    [txn] = data.transactions  # opening-balance group excluded
    assert txn.kind == "withdrawal" and txn.date == "2020-05-01"
    assert txn.external_id == "skrooge:op:7"
    [s] = txn.splits
    assert s.amount == Decimal("12.500000000000") and s.reconciled and s.tags == ("a",)
    assert data.budgets == [("Food", [(2026, 1, Decimal("500"))])]
