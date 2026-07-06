from decimal import Decimal

from skrooge2firefly.export.puller import PulledData
from skrooge2firefly.export.qif import ledger_entries, render_qif
from skrooge2firefly.export.verify import parse_qif, verify_qif, verify_skg
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


def test_parse_qif_roundtrips_counts_and_sums():
    a = acct("Checking", opening_balance=Decimal("100"), opening_balance_date="2010-01-01")
    txns = [
        Transaction(
            "x1", "withdrawal", "2020-05-01", [Split(Decimal("12.50"), "SEK", "Checking", "ICA")]
        ),
    ]
    text = render_qif([a], ledger_entries([a], txns))
    parsed = parse_qif(text)
    assert [amt for _, amt in parsed["Checking"]] == [Decimal("100.00"), Decimal("-12.50")]


def test_verify_qif_all_ok_and_detects_mismatch():
    a = acct("Checking")
    txns = [
        Transaction(
            "x1", "withdrawal", "2020-05-01", [Split(Decimal("12.50"), "SEK", "Checking", "ICA")]
        )
    ]
    good = render_qif([a], ledger_entries([a], txns))
    assert all(p.ok for p in verify_qif(good, [a], txns))
    bad = good.replace("T-12.50", "T-99.00")
    assert not all(p.ok for p in verify_qif(bad, [a], txns))


def test_verify_qif_transfer_counts_both_sides():
    a1, a2 = acct("Checking"), acct("Savings")
    txns = [
        Transaction(
            "t", "transfer", "2020-06-01", [Split(Decimal("500"), "SEK", "Checking", "Savings")]
        )
    ]
    text = render_qif([a1, a2], ledger_entries([a1, a2], txns))
    parities = {p.name: p for p in verify_qif(text, [a1, a2], txns)}
    assert parities["Checking"].ok and parities["Checking"].expected_count == 1
    assert parities["Savings"].ok and parities["Savings"].expected_sum == Decimal("500.00")


def test_verify_skg_roundtrip_ok_and_detects_damage(skrooge_template, tmp_path):
    from skrooge2firefly.export.skg import write_skg

    a1, a2 = acct("Checking"), acct("Savings")
    txns = [
        Transaction(
            "w", "withdrawal", "2020-01-02", [Split(Decimal("30"), "SEK", "Checking", "ICA")]
        ),
        Transaction(
            "t", "transfer", "2020-01-03", [Split(Decimal("500"), "SEK", "Checking", "Savings")]
        ),
    ]
    out = tmp_path / "rt.sqlite"
    write_skg(skrooge_template, out, PulledData([a1, a2], txns, []))
    assert all(p.ok for p in verify_skg(out, [a1, a2], txns))

    import sqlite3

    conn = sqlite3.connect(str(out))
    conn.execute("UPDATE suboperation SET f_value = f_value * 2 WHERE f_value = -30.0")
    conn.commit()
    conn.close()
    assert not all(p.ok for p in verify_skg(out, [a1, a2], txns))


def test_verify_qif_counts_liability_side_of_withdrawal():
    a1 = acct("Checking")
    a2 = acct("Mortgage", kind="liability", role=None, liability_type="loan")
    txns = [
        Transaction(
            "w", "withdrawal", "2015-01-01", [Split(Decimal("500"), "SEK", "Checking", "Mortgage")]
        )
    ]
    text = render_qif([a1, a2], ledger_entries([a1, a2], txns))
    parities = {p.name: p for p in verify_qif(text, [a1, a2], txns)}
    assert parities["Mortgage"].expected_count == 1
    assert parities["Mortgage"].ok and parities["Checking"].ok


def test_verify_skg_cross_currency_transfer_roundtrip(skrooge_template, tmp_path):
    """The destination side of a cross-currency transfer must be compared in
    the destination account's currency (foreign_amount), on BOTH sides."""
    from skrooge2firefly.export.skg import write_skg

    a1 = acct("CE Livret", "EUR")
    a2 = acct("Revolut SEK", "SEK")
    txns = [
        Transaction(
            "t",
            "transfer",
            "2020-01-03",
            [
                Split(
                    Decimal("1200"),
                    "EUR",
                    "CE Livret",
                    "Revolut SEK",
                    foreign_amount=Decimal("12000"),
                    foreign_currency_code="SEK",
                )
            ],
        ),
    ]
    out = tmp_path / "xc.sqlite"
    write_skg(skrooge_template, out, PulledData([a1, a2], txns, []))
    parities = {p.name: p for p in verify_skg(out, [a1, a2], txns)}
    assert parities["Revolut SEK"].expected_sum == Decimal("12000.00")
    assert parities["Revolut SEK"].ok, parities["Revolut SEK"]
    assert parities["CE Livret"].ok, parities["CE Livret"]
