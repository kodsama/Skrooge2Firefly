from decimal import Decimal

from skrooge2firefly.model.entities import Account, Split, Transaction
from skrooge2firefly.model.mapper import Mapper
from skrooge2firefly.verify import expected_accounts


def _mapper(transactions):
    m = Mapper()
    m.accounts = [
        Account("skrooge:acct:1", "Checking", "asset", "defaultAsset", "SEK", None, None, None),
        Account("skrooge:acct:2", "Savings", "asset", "savingAsset", "SEK", None, None, None),
        Account("skrooge:acct:3", "EuroAcc", "asset", "defaultAsset", "EUR", None, None, None),
    ]
    m.transactions = transactions
    return m


def test_withdrawal_and_deposit_effects():
    m = _mapper(
        [
            Transaction(
                "skrooge:op:1",
                "withdrawal",
                "2020-03-01",
                [Split(Decimal("12.50"), "SEK", "Checking", "ICA")],
            ),
            Transaction(
                "skrooge:op:2",
                "deposit",
                "2020-03-02",
                [Split(Decimal("100.00"), "SEK", "Employer", "Checking")],
            ),
        ]
    )
    exp = expected_accounts(m)
    assert exp["Checking"].balance == Decimal("87.50")
    assert exp["Checking"].count == 2
    assert exp["Checking"].by_month["2020-03"] == Decimal("87.50")
    assert exp["Checking"].by_year["2020"] == Decimal("87.50")
    assert "ICA" not in exp and "Employer" not in exp


def test_transfer_moves_between_two_accounts():
    m = _mapper(
        [
            Transaction(
                "skrooge:grp:1",
                "transfer",
                "2020-04-01",
                [Split(Decimal("500.00"), "SEK", "Checking", "Savings")],
            ),
        ]
    )
    exp = expected_accounts(m)
    assert exp["Checking"].balance == Decimal("-500.00")
    assert exp["Savings"].balance == Decimal("500.00")
    assert exp["Checking"].count == 1 and exp["Savings"].count == 1


def test_cross_currency_transfer_uses_foreign_amount_on_destination():
    m = _mapper(
        [
            Transaction(
                "skrooge:grp:2",
                "transfer",
                "2020-05-01",
                [
                    Split(
                        Decimal("1000.00"),
                        "SEK",
                        "Checking",
                        "EuroAcc",
                        foreign_amount=Decimal("90.00"),
                        foreign_currency_code="EUR",
                    )
                ],
            ),
        ]
    )
    exp = expected_accounts(m)
    assert exp["Checking"].balance == Decimal("-1000.00") and exp["Checking"].currency == "SEK"
    assert exp["EuroAcc"].balance == Decimal("90.00") and exp["EuroAcc"].currency == "EUR"


class FakeVerifyClient:
    """Stand-in exposing the read methods verify() needs."""

    def __init__(self, balances, group_count=0, account_txns=None):
        self._balances = balances  # name -> (id, Decimal, currency)
        self._group_count = group_count
        self._account_txns = account_txns or {}  # id -> list[dict]

    def account_balances(self, account_type):
        return dict(self._balances) if account_type == "asset" else {}

    def transaction_group_count(self):
        return self._group_count

    def account_transactions(self, account_id):
        return self._account_txns.get(account_id, [])


def test_verify_reports_match_and_mismatch():
    m = _mapper(
        [
            Transaction(
                "skrooge:op:1",
                "deposit",
                "2020-03-02",
                [Split(Decimal("100.00"), "SEK", "Employer", "Checking")],
            ),
            Transaction(
                "skrooge:op:2",
                "deposit",
                "2020-03-02",
                [Split(Decimal("50.00"), "SEK", "Employer", "Savings")],
            ),
        ]
    )
    client = FakeVerifyClient(
        balances={
            "Checking": ("1", Decimal("100.00"), "SEK"),
            "Savings": ("2", Decimal("0.00"), "SEK"),
        },
        group_count=2,
    )
    from skrooge2firefly.verify import verify

    report = verify(m, client, drill_down=False)
    by_name = {c.name: c for c in report.comparisons}
    assert by_name["Checking"].balance_ok is True
    assert by_name["Savings"].balance_ok is False
    assert by_name["Savings"].actual_balance == Decimal("0.00")
    assert report.all_ok is False
    assert report.expected_group_count == 2 and report.actual_group_count == 2


def test_verify_account_missing_on_server_is_mismatch():
    m = _mapper(
        [
            Transaction(
                "skrooge:op:1",
                "deposit",
                "2020-03-02",
                [Split(Decimal("100.00"), "SEK", "Employer", "Checking")],
            ),
        ]
    )
    client = FakeVerifyClient(balances={}, group_count=1)
    from skrooge2firefly.verify import verify

    report = verify(m, client, drill_down=False)
    c = report.comparisons[0]
    assert c.present is False and c.balance_ok is False


def test_verify_drill_down_finds_diverging_month():
    m = _mapper(
        [
            Transaction(
                "skrooge:op:1",
                "deposit",
                "2020-03-02",
                [Split(Decimal("100.00"), "SEK", "Employer", "Checking")],
            ),
        ]
    )
    client = FakeVerifyClient(
        balances={"Checking": ("1", Decimal("0.00"), "SEK")},
        group_count=1,
        account_txns={"1": []},
    )
    from skrooge2firefly.verify import verify

    report = verify(m, client, drill_down=True)
    c = report.comparisons[0]
    assert c.month_diffs == [("2020-03", Decimal("100.00"), Decimal("0.00"))]


def test_expected_accounts_credits_liability_side_of_withdrawal():
    """A loan payment (withdrawal into our liability) must move BOTH accounts."""
    from decimal import Decimal

    from skrooge2firefly.model.entities import Account, Split, Transaction
    from skrooge2firefly.model.mapper import Mapper
    from skrooge2firefly.verify import expected_accounts

    m = Mapper()
    m.accounts = [
        Account("a:1", "Checking", "asset", "defaultAsset", "SEK", None, None, None),
        Account("a:2", "Mortgage", "liability", None, "SEK", None, None, "loan"),
    ]
    m.transactions = [
        Transaction(
            "skrooge:grp:50",
            "withdrawal",
            "2015-01-01",
            [Split(Decimal("500"), "SEK", "Checking", "Mortgage")],
        ),
        Transaction(
            "skrooge:grp:60",
            "deposit",
            "2015-02-01",
            [Split(Decimal("20000"), "SEK", "Mortgage", "Checking")],
        ),
    ]
    exp = expected_accounts(m)
    assert exp["Checking"].balance == Decimal("19500")  # -500 + 20000
    assert exp["Mortgage"].balance == Decimal("-19500")  # +500 - 20000
    assert exp["Mortgage"].count == 2


def test_expected_accounts_quantizes_at_firefly_decimals():
    """Firefly stores per-currency decimals; the expected side must round the
    same way or thousands of float tails accumulate into false mismatches."""
    from decimal import Decimal

    from skrooge2firefly.model.entities import Account, Split, Transaction
    from skrooge2firefly.model.mapper import Mapper
    from skrooge2firefly.verify import expected_accounts

    m = Mapper()
    m.accounts = [Account("a:1", "Checking", "asset", "defaultAsset", "SEK", None, None, None)]
    m.transactions = [
        Transaction(
            "t1",
            "withdrawal",
            "2020-01-01",
            [Split(Decimal("9.999999E-8"), "SEK", "Checking", "X")],
        ),
        Transaction(
            "t2", "deposit", "2020-01-02", [Split(Decimal("10.005"), "SEK", "Y", "Checking")]
        ),
    ]
    exp = expected_accounts(m, currency_decimals={"SEK": 2})
    # -0.00 + 10.00 (banker's rounding at 2dp, exactly mirroring the writer)
    assert exp["Checking"].balance == Decimal("10.00")


def test_expected_accounts_flags_mixed_currency_account():
    from decimal import Decimal

    from skrooge2firefly.model.entities import Account, Split, Transaction
    from skrooge2firefly.model.mapper import Mapper
    from skrooge2firefly.verify import expected_accounts

    m = Mapper()
    m.accounts = [Account("a:1", "Kraken", "asset", "defaultAsset", "EUR", None, None, None)]
    m.transactions = [
        Transaction("t1", "deposit", "2020-01-01", [Split(Decimal("155.1"), "USD", "X", "Kraken")]),
    ]
    exp = expected_accounts(m)
    assert exp["Kraken"].mixed_currency is True


def test_expected_accounts_includes_untouched_accounts_seeded_from_opening_balance():
    """An account with zero transactions (e.g. a loan whose only Skrooge
    operation is its opening balance) must still be verified, not silently
    dropped from the expected set."""
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:9",
            "Loan 2",
            "liability",
            None,
            "SEK",
            Decimal("-1355000.00"),
            "1900-01-01",
            "loan",
        ),
    ]
    m.transactions = []
    exp = expected_accounts(m)
    assert "Loan 2" in exp
    assert exp["Loan 2"].balance == Decimal("-1355000.00")
    assert exp["Loan 2"].count == 0


def test_expected_accounts_opening_balance_seeds_touched_account_too():
    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            Decimal("100.00"),
            "2010-01-01",
            None,
        ),
    ]
    m.transactions = [
        Transaction(
            "skrooge:op:1",
            "withdrawal",
            "2020-03-01",
            [Split(Decimal("12.50"), "SEK", "Checking", "ICA")],
        ),
    ]
    exp = expected_accounts(m)
    assert exp["Checking"].balance == Decimal("87.50")  # 100 - 12.50


def test_expected_group_count_excludes_opening_balance_journals():
    """Firefly does create an "Opening balance" journal per account with
    opening_balance set, but its general /transactions listing (what
    transaction_group_count() reads) excludes opening-balance/reconciliation
    journal types — confirmed empirically against a live instance — so the
    journal-count total must NOT count them."""
    from skrooge2firefly.verify import verify

    class StubClient:
        def account_balances(self, account_type):
            return {}

        def transaction_group_count(self):
            return 0  # the opening-balance journal is excluded from this total

        def currency_decimals(self):
            return {}

    m = Mapper()
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Loan",
            "liability",
            None,
            "SEK",
            Decimal("-500.00"),
            "1900-01-01",
            "loan",
        ),
    ]
    m.transactions = []
    report = verify(m, StubClient(), drill_down=False)
    assert report.expected_group_count == 0
    assert report.actual_group_count == 0


# ── Mandatory independent Skrooge-vs-Firefly latest-balance check ────────────


def test_latest_balance_check_flags_missing_opening_balance():
    """The core value of the independent check: if Skrooge says an account
    holds -1,355,000 but Firefly shows 0 (the opening-balance bug), verify
    must FAIL — even though that account has zero transactions."""
    from skrooge2firefly.skrooge.reader import AccountAudit
    from skrooge2firefly.verify import verify

    m = Mapper()
    m.accounts = [
        Account("skrooge:acct:9", "Loan", "liability", None, "SEK", None, None, "loan"),
    ]
    m.transactions = []
    m.account_audits = {
        "Loan": AccountAudit("Loan", -1355000.0, "SEK", False, False, False),
    }

    class StubClient:
        def account_balances(self, account_type):
            # Firefly wrongly shows 0 for the loan (opening balance dropped).
            return {"Loan": ("77", Decimal("0.00"), "SEK")} if account_type == "liability" else {}

        def transaction_group_count(self):
            return 0

        def currency_decimals(self):
            return {}

    report = verify(m, StubClient(), drill_down=False)
    assert report.all_ok is False
    bad = [b for b in report.balance_parities if not b.ok]
    assert [b.name for b in bad] == ["Loan"]
    assert bad[0].skrooge_balance == Decimal("-1355000.00")
    assert bad[0].firefly_balance == Decimal("0.00")


def test_latest_balance_check_passes_when_balances_match():
    from skrooge2firefly.skrooge.reader import AccountAudit
    from skrooge2firefly.verify import verify

    m = Mapper()
    # opening_balance makes the IR-side expectation agree too, isolating the
    # independent balance check under test.
    m.accounts = [
        Account(
            "skrooge:acct:1",
            "Checking",
            "asset",
            "defaultAsset",
            "SEK",
            Decimal("87.50"),
            "2010-01-01",
            None,
        )
    ]
    m.transactions = []
    m.account_audits = {"Checking": AccountAudit("Checking", 87.50, "SEK", False, False, False)}

    class StubClient:
        def account_balances(self, account_type):
            return {"Checking": ("1", Decimal("87.50"), "SEK")} if account_type == "asset" else {}

        def transaction_group_count(self):
            return 0

        def currency_decimals(self):
            return {}

    report = verify(m, StubClient(), drill_down=False)
    assert all(b.ok for b in report.balance_parities)
    assert report.all_ok is True


def test_latest_balance_check_share_account_must_match_cash():
    """Since share/fund cash is now preserved (as tagged Securities expenses),
    a share-trading account is NO LONGER excused: its cash balance must match,
    and a mismatch must FAIL the run."""
    from skrooge2firefly.skrooge.reader import AccountAudit
    from skrooge2firefly.verify import verify

    m = Mapper()
    m.accounts = [
        Account("skrooge:acct:1", "ISK", "asset", "defaultAsset", "SEK", None, None, None),
    ]
    m.transactions = []
    # has_shares=True but cash 303808.45 vs Firefly's (wrong) 1308449.50.
    m.account_audits = {"ISK": AccountAudit("ISK", 303808.45, "SEK", True, False, False)}

    class StubClient:
        def account_balances(self, account_type):
            return {"ISK": ("1", Decimal("1308449.50"), "SEK")} if account_type == "asset" else {}

        def transaction_group_count(self):
            return 0

        def currency_decimals(self):
            return {}

    report = verify(m, StubClient(), drill_down=False)
    isk = {b.name: b for b in report.balance_parities}["ISK"]
    assert isk.informational is False  # shares no longer excuse a mismatch
    assert isk.ok is False
    assert report.all_ok is False


def test_latest_balance_check_informational_only_for_mixed_and_legs():
    """Mixed-currency and inconsistent-transfer-leg accounts genuinely can't
    match; their differences are informational and must NOT fail the run."""
    from skrooge2firefly.skrooge.reader import AccountAudit
    from skrooge2firefly.verify import verify

    m = Mapper()
    # opening_balance set to Firefly's value so the IR-side check is satisfied,
    # isolating the independent balance check's informational classification.
    m.accounts = [
        Account(
            "skrooge:acct:2",
            "Kraken",
            "asset",
            "defaultAsset",
            "EUR",
            Decimal("568.98"),
            "2010-01-01",
            None,
        ),
        Account(
            "skrooge:acct:3",
            "ExtraLoan",
            "liability",
            None,
            "SEK",
            Decimal("268.00"),
            "2010-01-01",
            "loan",
        ),
    ]
    m.transactions = []
    m.account_audits = {
        "Kraken": AccountAudit("Kraken", 547.34, "EUR", False, True, False),
        "ExtraLoan": AccountAudit("ExtraLoan", 0.0, "SEK", False, False, True),
    }

    class StubClient:
        def account_balances(self, account_type):
            if account_type == "asset":
                return {"Kraken": ("2", Decimal("568.98"), "EUR")}
            return {"ExtraLoan": ("3", Decimal("268.00"), "SEK")}

        def transaction_group_count(self):
            return 0

        def currency_decimals(self):
            return {}

    report = verify(m, StubClient(), drill_down=False)
    parities = {b.name: b for b in report.balance_parities}
    assert parities["Kraken"].informational and not parities["Kraken"].ok
    assert parities["ExtraLoan"].informational and not parities["ExtraLoan"].ok
    assert report.all_ok is True  # informational-only differences don't fail
