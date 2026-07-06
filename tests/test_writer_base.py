from skrooge2firefly.writers.base import WriteReport


def test_report_counts_and_summary():
    report = WriteReport()
    report.created("transaction")
    report.created("transaction")
    report.skipped("transaction")
    report.failed("account", "boom")
    assert report.counts["transaction"]["created"] == 2
    assert report.counts["transaction"]["skipped"] == 1
    assert report.counts["account"]["failed"] == 1
    assert report.errors == [("account", "boom")]
    assert "transaction" in report.summary()
    assert report.has_failures is True


def test_report_tracks_updated():
    from skrooge2firefly.writers.base import WriteReport

    report = WriteReport()
    report.updated("transaction")
    report.updated("transaction")
    assert report.counts["transaction"]["updated"] == 2
    assert "updated" in report.summary()
