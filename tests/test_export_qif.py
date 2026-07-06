from decimal import Decimal

from skrooge2firefly.export.qif import QifEntry, ledger_entries, render_qif
from skrooge2firefly.model.entities import Account, Split, Transaction


def acct(name, currency="SEK", **kw):
    defaults = dict(
        external_id=f"firefly:acct:{name}",
        name=name,
        kind="asset",
        role="defaultAsset",
        currency_code=currency,
        opening_balance=None,
        opening_balance_date=None,
        liability_type=None,
    )
    defaults.update(kw)
    return Account(**defaults)


def test_withdrawal_and_deposit_signs():
    accounts = [acct("Checking")]
    txns = [
        Transaction(
            "x1",
            "withdrawal",
            "2020-05-01",
            [Split(Decimal("12.50"), "SEK", "Checking", "ICA", category_name="Food")],
        ),
        Transaction(
            "x2", "deposit", "2020-05-02", [Split(Decimal("100"), "SEK", "Employer", "Checking")]
        ),
    ]
    entries = ledger_entries(accounts, txns)
    w, d = entries["Checking"]
    assert w.amount == Decimal("-12.50") and w.payee == "ICA" and w.category == "Food"
    assert d.amount == Decimal("100") and d.payee == "Employer"


def test_transfer_appears_in_both_accounts_with_brackets():
    accounts = [acct("Checking"), acct("Savings")]
    txns = [
        Transaction(
            "t1", "transfer", "2020-06-01", [Split(Decimal("500"), "SEK", "Checking", "Savings")]
        )
    ]
    entries = ledger_entries(accounts, txns)
    [out] = entries["Checking"]
    [inn] = entries["Savings"]
    assert out.amount == Decimal("-500") and out.transfer_to == "Savings"
    assert inn.amount == Decimal("500") and inn.transfer_to == "Checking"


def test_cross_currency_transfer_uses_foreign_amount_on_destination():
    accounts = [acct("Checking", "SEK"), acct("EuroAcct", "EUR")]
    txns = [
        Transaction(
            "t2",
            "transfer",
            "2020-06-02",
            [
                Split(
                    Decimal("100"),
                    "SEK",
                    "Checking",
                    "EuroAcct",
                    foreign_amount=Decimal("9.10"),
                    foreign_currency_code="EUR",
                )
            ],
        )
    ]
    entries = ledger_entries(accounts, txns)
    assert entries["Checking"][0].amount == Decimal("-100")
    assert entries["EuroAcct"][0].amount == Decimal("9.10")


def test_multi_split_entry_totals_and_splits():
    accounts = [acct("Checking")]
    txns = [
        Transaction(
            "m1",
            "withdrawal",
            "2020-07-01",
            [
                Split(Decimal("10"), "SEK", "Checking", "ICA", category_name="A > B"),
                Split(Decimal("2.50"), "SEK", "Checking", "ICA", category_name="C"),
            ],
            group_title="Big shop",
        )
    ]
    [e] = ledger_entries(accounts, txns)["Checking"]
    assert e.amount == Decimal("-12.50")
    assert e.splits == (("A:B", Decimal("-10"), ""), ("C", Decimal("-2.50"), ""))


def test_render_qif_block_structure_and_fields():
    a = acct("Checking")
    entries = {
        "Checking": [
            QifEntry("2020-05-01", Decimal("-12.50"), "ICA", "Food:Groceries", "memo", True)
        ]
    }
    text = render_qif([a], entries)
    assert "!Account\nNChecking\nTBank\n^\n!Type:Bank\n" in text
    block = text.split("!Type:Bank\n", 1)[1]
    assert "D2020-05-01\nT-12.50\nPICA\nLFood:Groceries\nMmemo\nCR\n^" in block


def test_render_qif_account_types_and_opening_balance():
    cc = acct("Card", role="ccAsset")
    cash = acct("Wallet", role="cashWalletAsset")
    loan = acct(
        "Mortgage",
        kind="liability",
        role=None,
        liability_type="loan",
        opening_balance=Decimal("-1000"),
        opening_balance_date="2010-01-01",
    )
    text = render_qif([cc, cash, loan], {"Card": [], "Wallet": [], "Mortgage": []})
    assert "NCard\nTCCard" in text
    assert "NWallet\nTCash" in text
    assert "NMortgage\nTOth L" in text
    assert "D2010-01-01\nT-1000.00\nPOpening Balance\nL[Mortgage]\n^" in text


def test_render_qif_splits_and_transfer():
    a = acct("Checking")
    entries = {
        "Checking": [
            QifEntry(
                "2020-07-01",
                Decimal("-12.50"),
                "ICA",
                None,
                "",
                False,
                splits=(("A:B", Decimal("-10"), "x"), ("C", Decimal("-2.50"), "")),
            ),
            QifEntry(
                "2020-08-01", Decimal("-500"), "Savings", None, "", False, transfer_to="Savings"
            ),
        ]
    }
    text = render_qif([a], entries)
    assert "SA:B\nEx\n$-10.00\nSC\n$-2.50\n" in text
    assert "L[Savings]" in text


def test_withdrawal_into_own_liability_lands_in_both_blocks():
    accounts = [
        acct("Checking"),
        acct("Mortgage", kind="liability", role=None, liability_type="loan"),
    ]
    txns = [
        Transaction(
            "w", "withdrawal", "2015-01-01", [Split(Decimal("500"), "SEK", "Checking", "Mortgage")]
        )
    ]
    entries = ledger_entries(accounts, txns)
    [out] = entries["Checking"]
    [inn] = entries["Mortgage"]
    assert out.amount == Decimal("-500") and out.transfer_to == "Mortgage"
    assert inn.amount == Decimal("500") and inn.transfer_to == "Checking"


def test_deposit_from_own_liability_lands_in_both_blocks():
    accounts = [
        acct("Checking"),
        acct("Mortgage", kind="liability", role=None, liability_type="loan"),
    ]
    txns = [
        Transaction(
            "d", "deposit", "2015-02-01", [Split(Decimal("20000"), "SEK", "Mortgage", "Checking")]
        )
    ]
    entries = ledger_entries(accounts, txns)
    [inn] = entries["Checking"]
    [out] = entries["Mortgage"]
    assert inn.amount == Decimal("20000") and inn.transfer_to == "Mortgage"
    assert out.amount == Decimal("-20000") and out.transfer_to == "Checking"
