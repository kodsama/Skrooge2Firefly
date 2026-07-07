import pytest
import responses

from skrooge2firefly.writers.client import DuplicateTransactionError, FireflyClient, FireflyError

BASE = "https://firefly.example.com"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retry backoff must not really sleep in tests."""
    monkeypatch.setattr("skrooge2firefly.writers.client.time.sleep", lambda _s: None)


@pytest.fixture
def client() -> FireflyClient:
    return FireflyClient(BASE, "token-123")


@responses.activate
def test_get_about_sends_auth_header(client: FireflyClient):
    responses.add(
        responses.GET, f"{BASE}/api/v1/about", json={"data": {"version": "6.2.0"}}, status=200
    )
    assert client.get_version() == "6.2.0"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer token-123"


@responses.activate
def test_store_account_returns_id(client: FireflyClient):
    responses.add(
        responses.POST, f"{BASE}/api/v1/accounts", json={"data": {"id": "42"}}, status=200
    )
    assert client.store_account({"name": "Checking"}) == "42"


@responses.activate
def test_store_transaction_raises_on_duplicate(client: FireflyClient):
    """422 with 'Duplicate of transaction' in errors → DuplicateTransactionError (Fix A4)."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/transactions",
        json={
            "message": "The given data was invalid.",
            "errors": {"transactions.0.description": ["Duplicate of transaction #5."]},
        },
        status=422,
    )
    with pytest.raises(DuplicateTransactionError):
        client.store_transaction({"transactions": []})


@responses.activate
def test_store_transaction_duplicate_in_top_level_message(client: FireflyClient):
    """422 whose duplicate notice is ONLY in the top-level message → DuplicateTransactionError."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/transactions",
        json={"message": "Duplicate of transaction #5.", "errors": {}},
        status=422,
    )
    with pytest.raises(DuplicateTransactionError):
        client.store_transaction({"transactions": []})


@responses.activate
def test_store_transaction_422_non_duplicate_raises_firefly_error(client: FireflyClient):
    """422 with an unrelated validation error raises FireflyError, not DuplicateTransactionError."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/transactions",
        json={
            "message": "The given data was invalid.",
            "errors": {"transactions.0.amount": ["The amount must be greater than zero."]},
        },
        status=422,
    )
    with pytest.raises(FireflyError) as exc_info:
        client.store_transaction({"transactions": []})
    assert not isinstance(exc_info.value, DuplicateTransactionError)


@responses.activate
def test_list_currency_codes(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/currencies",
        json={
            "data": [{"attributes": {"code": "EUR"}}, {"attributes": {"code": "SEK"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.list_currency_codes() == {"EUR", "SEK"}


@responses.activate
def test_find_account_id_returns_id_on_exact_match(client: FireflyClient):
    """find_account_id returns the account id when the name matches exactly."""
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/search/accounts",
        json={
            "data": [
                {"id": "42", "attributes": {"name": "Checking"}},
                {"id": "43", "attributes": {"name": "Checking (old)"}},
            ]
        },
        status=200,
    )
    assert client.find_account_id("Checking") == "42"


@responses.activate
def test_find_account_id_returns_none_when_no_match(client: FireflyClient):
    """find_account_id returns None when no item has an exactly matching name."""
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/search/accounts",
        json={"data": [{"id": "99", "attributes": {"name": "Other Account"}}]},
        status=200,
    )
    assert client.find_account_id("Checking") is None


@responses.activate
def test_store_budget_and_limit_and_currency(client: FireflyClient) -> None:
    """store_budget, store_budget_limit and store_currency hit the right paths."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/budgets",
        json={"data": {"id": "7"}},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/budgets/7/limits",
        json={},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/currencies",
        json={},
        status=200,
    )

    budget_id = client.store_budget({"name": "Food", "active": True})
    assert budget_id == "7"

    client.store_budget_limit("7", {"start": "2020-01-01", "end": "2020-01-31", "amount": "500.00"})
    # The budget-limit call must target the nested URL
    limit_url = responses.calls[1].request.url
    assert "/api/v1/budgets/7/limits" in limit_url

    # store_currency returns None but must not raise
    client.store_currency(
        {
            "code": "SEK",
            "name": "Swedish Krona",
            "symbol": "kr",
            "decimal_places": 2,
            "enabled": True,
        }
    )


@responses.activate
def test_store_recurrence_posts_and_returns_id(client: FireflyClient) -> None:
    """store_recurrence POSTs to /recurrences and returns the id."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/recurrences",
        json={"data": {"id": "42"}},
        status=200,
    )
    assert client.store_recurrence({"title": "Rent"}) == "42"


@responses.activate
def test_recurrence_index_maps_title_to_id(client: FireflyClient) -> None:
    """recurrence_index returns a title→id mapping from a paged recurrences response."""
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/recurrences",
        json={
            "data": [
                {"id": "1", "attributes": {"title": "Rent"}},
                {"id": "2", "attributes": {"title": "Gym"}},
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.recurrence_index() == {"Rent": "1", "Gym": "2"}


@responses.activate
def test_post_non_ok_raises_fireflyerror(client: FireflyClient) -> None:
    """A non-2xx response from a POST endpoint raises FireflyError."""
    responses.add(
        responses.POST,
        f"{BASE}/api/v1/budgets",
        body="Internal Server Error",
        status=500,
    )
    with pytest.raises(FireflyError):
        client.store_budget({"name": "Broken"})


@responses.activate
def test_account_index_maps_name_to_id(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={
            "data": [
                {"id": "1", "attributes": {"name": "Checking"}},
                {"id": "2", "attributes": {"name": "Savings"}},
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.account_index() == {"Checking": "1", "Savings": "2"}


@responses.activate
def test_budget_index_maps_name_to_id(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/budgets",
        json={
            "data": [{"id": "7", "attributes": {"name": "Food"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.budget_index() == {"Food": "7"}


@responses.activate
def test_existing_external_ids_collects_prefixed_ids(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/transactions",
        json={
            "data": [
                {
                    "id": "1",
                    "attributes": {
                        "transactions": [
                            {"external_id": "skrooge:op:1"},
                            {"external_id": "skrooge:op:1"},
                        ]
                    },
                },
                {
                    "id": "2",
                    "attributes": {
                        "transactions": [
                            {"external_id": "manual-entry"},
                            {"external_id": None},
                            {"external_id": "skrooge:grp:9"},
                        ]
                    },
                },
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.existing_external_ids() == {"skrooge:op:1", "skrooge:grp:9"}


@responses.activate
def test_account_balances_maps_name_to_id_balance_currency(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={
            "data": [
                {
                    "id": "1",
                    "attributes": {
                        "name": "Checking",
                        "current_balance": "87.50",
                        "currency_code": "SEK",
                    },
                },
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    from decimal import Decimal

    bals = client.account_balances("asset")
    assert bals["Checking"] == ("1", Decimal("87.50"), "SEK")
    assert "date=" in responses.calls[0].request.url


@responses.activate
def test_transaction_group_count_reads_pagination_total(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/transactions",
        json={
            "data": [],
            "meta": {"pagination": {"total": 18544, "total_pages": 1, "current_page": 1}},
        },
        status=200,
    )
    assert client.transaction_group_count() == 18544


@responses.activate
def test_account_transactions_paged(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts/5/transactions",
        json={
            "data": [{"id": "9", "attributes": {"transactions": [{"amount": "10.00"}]}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    rows = client.account_transactions("5")
    assert rows[0]["attributes"]["transactions"][0]["amount"] == "10.00"


@responses.activate
def test_external_id_to_group_id_maps_prefixed(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/transactions",
        json={
            "data": [
                {"id": "100", "attributes": {"transactions": [{"external_id": "skrooge:op:1"}]}},
                {
                    "id": "101",
                    "attributes": {
                        "transactions": [
                            {"external_id": "skrooge:grp:2"},
                            {"external_id": "skrooge:grp:2"},
                        ]
                    },
                },
                {"id": "102", "attributes": {"transactions": [{"external_id": "manual"}]}},
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.external_id_to_group_id() == {"skrooge:op:1": "100", "skrooge:grp:2": "101"}


@responses.activate
def test_account_states_maps_name_to_id_and_active(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={
            "data": [{"id": "5", "attributes": {"name": "Checking", "active": True}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.account_states("asset") == {"Checking": ("5", True)}


@responses.activate
def test_update_transaction_puts(client: FireflyClient):
    responses.add(
        responses.PUT,
        f"{BASE}/api/v1/transactions/100",
        json={"data": {"id": "100"}},
        status=200,
    )
    client.update_transaction("100", {"transactions": []})
    assert responses.calls[0].request.method == "PUT"


@responses.activate
def test_update_account_puts(client: FireflyClient):
    responses.add(
        responses.PUT,
        f"{BASE}/api/v1/accounts/5",
        json={"data": {"id": "5"}},
        status=200,
    )
    client.update_account("5", {"name": "Checking", "active": False})
    assert responses.calls[0].request.method == "PUT"


# --- transient-error retry behaviour ---


@pytest.fixture
def fast_retry_client() -> FireflyClient:
    """A client with a small retry budget for fast retry-path tests."""
    return FireflyClient(BASE, "token-123", max_retries=2)


@responses.activate
def test_get_retries_502_then_succeeds(fast_retry_client: FireflyClient):
    responses.add(responses.GET, f"{BASE}/api/v1/about", json={}, status=502)
    responses.add(
        responses.GET, f"{BASE}/api/v1/about", json={"data": {"version": "6.6.3"}}, status=200
    )
    assert fast_retry_client.get_version() == "6.6.3"
    assert len(responses.calls) == 2


@responses.activate
def test_get_gives_up_after_max_retries(fast_retry_client: FireflyClient):
    for _ in range(3):  # initial attempt + 2 retries
        responses.add(responses.GET, f"{BASE}/api/v1/about", json={}, status=503)
    with pytest.raises(Exception):  # noqa: B017 - any HTTP error is acceptable here
        fast_retry_client.get_version()
    assert len(responses.calls) == 3


@responses.activate
def test_store_transaction_retry_detects_already_landed(fast_retry_client: FireflyClient):
    """A 502 may hide a commit: before re-POSTing, the landed-check must find it."""
    responses.add(responses.POST, f"{BASE}/api/v1/transactions", json={}, status=502)
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/search/transactions",
        json={
            "data": [
                {
                    "id": "77",
                    "attributes": {"transactions": [{"external_id": "skrooge:op:9"}]},
                }
            ]
        },
        status=200,
    )
    payload = {"transactions": [{"external_id": "skrooge:op:9", "amount": "1.00"}]}
    assert fast_retry_client.store_transaction(payload) == "77"
    methods = [c.request.method for c in responses.calls]
    assert methods == ["POST", "GET"]  # no second POST → no duplicate


@responses.activate
def test_store_transaction_retries_502_when_not_landed(fast_retry_client: FireflyClient):
    responses.add(responses.POST, f"{BASE}/api/v1/transactions", json={}, status=502)
    responses.add(
        responses.GET, f"{BASE}/api/v1/search/transactions", json={"data": []}, status=200
    )
    responses.add(
        responses.POST, f"{BASE}/api/v1/transactions", json={"data": {"id": "42"}}, status=200
    )
    payload = {"transactions": [{"external_id": "skrooge:op:9", "amount": "1.00"}]}
    assert fast_retry_client.store_transaction(payload) == "42"


@responses.activate
def test_store_transaction_gives_up_after_max_retries(fast_retry_client: FireflyClient):
    for _ in range(3):
        responses.add(responses.POST, f"{BASE}/api/v1/transactions", json={}, status=502)
        responses.add(
            responses.GET, f"{BASE}/api/v1/search/transactions", json={"data": []}, status=200
        )
    payload = {"transactions": [{"external_id": "skrooge:op:9", "amount": "1.00"}]}
    with pytest.raises(FireflyError, match="502"):
        fast_retry_client.store_transaction(payload)


@responses.activate
def test_get_retries_empty_200_body(fast_retry_client: FireflyClient):
    """The origin can return a bodyless 200 from a sick worker; treat as transient."""
    responses.add(responses.GET, f"{BASE}/api/v1/about", body="", status=200)
    responses.add(
        responses.GET, f"{BASE}/api/v1/about", json={"data": {"version": "6.6.3"}}, status=200
    )
    assert fast_retry_client.get_version() == "6.6.3"
    assert len(responses.calls) == 2


@responses.activate
def test_store_transaction_retries_empty_200_with_landed_check(fast_retry_client: FireflyClient):
    responses.add(responses.POST, f"{BASE}/api/v1/transactions", body="", status=200)
    responses.add(
        responses.GET, f"{BASE}/api/v1/search/transactions", json={"data": []}, status=200
    )
    responses.add(
        responses.POST, f"{BASE}/api/v1/transactions", json={"data": {"id": "42"}}, status=200
    )
    payload = {"transactions": [{"external_id": "skrooge:op:9", "amount": "1.00"}]}
    assert fast_retry_client.store_transaction(payload) == "42"


def test_retry_delay_is_short_for_empty_200():
    from skrooge2firefly.writers.client import _retry_delay

    class FakeResp:
        ok = True
        content = b""
        status_code = 200
        headers: dict = {}

    assert _retry_delay(0, FakeResp()) <= 1.0
    assert _retry_delay(7, FakeResp()) <= 5.0


def test_retry_delay_honours_retry_after():
    from skrooge2firefly.writers.client import _retry_delay

    class FakeResp:
        ok = False
        content = b"x"
        status_code = 503
        headers = {"Retry-After": "45"}

    assert _retry_delay(0, FakeResp()) == 45.0
    assert _retry_delay(0, None) == 2.0  # connection error: plain exponential


def test_empty_200_backoff_evicts_pinned_connections(monkeypatch):
    """Empty-200s stick to a pinned upstream connection; retry must dial fresh."""
    client = FireflyClient(BASE, "token-123")
    closed = []
    monkeypatch.setattr(client._session, "close", lambda: closed.append(True))

    class EmptyOk:
        ok = True
        content = b""
        status_code = 200
        headers: dict = {}

    class Gateway502:
        ok = False
        content = b"bad gateway"
        status_code = 502
        headers: dict = {}

    client._backoff(0, EmptyOk(), "GET about")
    assert closed == [True]
    client._backoff(0, Gateway502(), "GET about")
    assert closed == [True]  # 502 keeps the pool: server-side, not connection-side


# --- export listing methods ---


@responses.activate
def test_list_accounts_returns_raw_items(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/accounts",
        json={
            "data": [{"id": "1", "attributes": {"name": "Checking", "type": "asset"}}],
            "meta": {"pagination": {"total_pages": 1}},
        },
        status=200,
    )
    items = client.list_accounts("asset")
    assert items[0]["attributes"]["name"] == "Checking"


@responses.activate
def test_list_transaction_groups_returns_raw_items(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/transactions",
        json={
            "data": [{"id": "9", "attributes": {"transactions": []}}],
            "meta": {"pagination": {"total_pages": 1}},
        },
        status=200,
    )
    assert client.list_transaction_groups()[0]["id"] == "9"


@responses.activate
def test_list_budgets_with_limits(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/budgets",
        json={
            "data": [{"id": "3", "attributes": {"name": "Food"}}],
            "meta": {"pagination": {"total_pages": 1}},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/budgets/3/limits",
        json={
            "data": [{"attributes": {"start": "2026-01-01", "end": "2026-01-31", "amount": "500"}}],
            "meta": {"pagination": {"total_pages": 1}},
        },
        status=200,
    )
    [(name, limits)] = client.list_budgets_with_limits()
    assert name == "Food" and limits[0]["attributes"]["amount"] == "500"


@responses.activate
def test_get_retries_cloudflare_52x_then_succeeds(fast_retry_client: FireflyClient):
    """Cloudflare 520/521 (origin down/refusing) are transient: origin restarts recover them."""
    responses.add(responses.GET, f"{BASE}/api/v1/about", json={}, status=521)
    responses.add(responses.GET, f"{BASE}/api/v1/about", json={}, status=520)
    responses.add(
        responses.GET, f"{BASE}/api/v1/about", json={"data": {"version": "6.6.6"}}, status=200
    )
    assert fast_retry_client.get_version() == "6.6.6"
    assert len(responses.calls) == 3


@responses.activate
def test_currency_decimals(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/currencies",
        json={
            "data": [
                {"id": "1", "attributes": {"code": "SEK", "decimal_places": 2}},
                {"id": "2", "attributes": {"code": "BTC", "decimal_places": 6}},
            ],
            "meta": {"pagination": {"total_pages": 1}},
        },
        status=200,
    )
    assert client.currency_decimals() == {"SEK": 2, "BTC": 6}


@responses.activate
def test_delete_204_is_not_transient(fast_retry_client: FireflyClient):
    """204 No Content is a legitimate empty success, not a sick-worker symptom."""
    responses.add(responses.DELETE, f"{BASE}/api/v1/transactions/5", status=204)
    resp = fast_retry_client._send("DELETE", "transactions/5")
    assert resp.status_code == 204
    assert len(responses.calls) == 1


# --- content reads and delete/update methods (upsert change detection) ---


@responses.activate
def test_transactions_by_external_id_carries_splits(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/transactions",
        json={
            "data": [
                {
                    "id": "100",
                    "attributes": {
                        "transactions": [
                            {
                                "external_id": "skrooge:op:1",
                                "amount": "12.000000",
                                "description": "ICA",
                            }
                        ]
                    },
                }
            ],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    got = client.transactions_by_external_id()
    assert got["skrooge:op:1"]["group_id"] == "100"
    assert got["skrooge:op:1"]["splits"][0]["description"] == "ICA"


@responses.activate
def test_delete_transaction_issues_delete(client: FireflyClient):
    responses.add(responses.DELETE, f"{BASE}/api/v1/transactions/100", status=204)
    client.delete_transaction("100")
    assert responses.calls[-1].request.method == "DELETE"


@responses.activate
def test_update_recurrence_puts(client: FireflyClient):
    responses.add(
        responses.PUT, f"{BASE}/api/v1/recurrences/9", json={"data": {"id": "9"}}, status=200
    )
    client.update_recurrence("9", {"title": "Rent"})
    assert responses.calls[-1].request.method == "PUT"


@responses.activate
def test_recurrences_full_maps_title(client: FireflyClient):
    responses.add(
        responses.GET,
        f"{BASE}/api/v1/recurrences",
        json={
            "data": [{"id": "9", "attributes": {"title": "Rent", "notes": "x"}}],
            "meta": {"pagination": {"current_page": 1, "total_pages": 1}},
        },
        status=200,
    )
    assert client.recurrences_full()["Rent"]["id"] == "9"
