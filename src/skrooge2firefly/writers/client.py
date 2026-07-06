"""A thin Firefly-III REST client (only the endpoints this tool needs)."""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# Transient statuses worth retrying: origin overload/gateway errors, rate limits,
# and Cloudflare's 52x origin-error family (origin crashed/refusing while it restarts).
_RETRY_STATUSES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _retry_delay(attempt: int, resp: requests.Response | None) -> float:
    """Return how long to sleep before retry ``attempt`` for this failure.

    Empty-200 responses (see :func:`_is_transient`) come back instantly from a
    sick worker and a healthy one is usually a round-robin hop away, so they get
    quick, short retries. Everything else backs off exponentially, honouring a
    Retry-After header when present.
    """
    if resp is not None and resp.ok and not resp.content:
        return float(min(0.5 * 2**attempt, 5.0))
    delay = float(min(2 ** (attempt + 1), 60))
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            delay = max(delay, min(float(retry_after), 120.0))
    return delay


def _is_transient(resp: requests.Response) -> bool:
    """Return True for responses that should be retried.

    Besides gateway/overload statuses, an overloaded origin has been observed
    returning HTTP 200 with an *empty* body from a sick worker; every endpoint
    this client uses returns JSON, so an empty success body is also transient.
    """
    if resp.status_code in _RETRY_STATUSES:
        return True
    return resp.ok and resp.status_code != 204 and not resp.content


class FireflyError(RuntimeError):
    """A non-recoverable Firefly API error."""


class DuplicateTransactionError(FireflyError):
    """Raised when Firefly rejects a transaction as a duplicate (HTTP 422)."""


class FireflyClient:
    """Wraps a Firefly-III instance's v1 API."""

    def __init__(
        self, base_url: str, token: str, *, timeout: float = 30.0, max_retries: int = 8
    ) -> None:
        """Create a client.

        Args:
            base_url: Base URL of the Firefly instance, e.g. ``https://firefly.example.com``.
            token: A Firefly Personal Access Token.
            timeout: Per-request timeout in seconds.
            max_retries: Retries per request on transient errors (429/5xx, connection drops).

        """
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _url(self, path: str) -> str:
        return f"{self._base}/api/v1/{path.lstrip('/')}"

    def _backoff(self, attempt: int, resp: requests.Response | None, context: str) -> None:
        """Sleep before retry ``attempt`` (see :func:`_retry_delay`).

        Empty-200 responses have been observed to stick to whichever upstream
        connection they arrived on (a sick backend behind the proxy), so the
        pooled connections are evicted to make the retry dial a fresh one —
        which can land on a healthy backend and stay there via keep-alive.
        """
        delay = _retry_delay(attempt, resp)
        status = resp.status_code if resp is not None else "connection error"
        empty_ok = resp is not None and resp.ok and not resp.content
        if empty_ok:
            self._session.close()  # evict pooled (possibly pinned) connections
        detail = " empty body;" if empty_ok else ""
        logger.warning(
            "%s: transient error (%s);%s retrying in %.1fs", context, status, detail, delay
        )
        time.sleep(delay)

    def _send(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Send a request, retrying transient failures with exponential backoff.

        Returns the final response (which may still be an error response for
        non-transient statuses); raises the last connection error if every
        attempt failed to reach the server.
        """
        last_exc: requests.RequestException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.request(
                    method, self._url(path), timeout=self._timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == self._max_retries:
                    raise
                self._backoff(attempt, None, f"{method} {path}")
                continue
            if _is_transient(resp) and attempt < self._max_retries:
                self._backoff(attempt, resp, f"{method} {path}")
                continue
            return resp
        raise last_exc  # type: ignore[misc]  # unreachable: loop always returns or raises

    def get_version(self) -> str:
        """Return the Firefly version string (also validates auth/connectivity)."""
        resp = self._send("GET", "about")
        if resp.status_code == 401:
            raise FireflyError("Authentication failed (401): check FIREFLY_TOKEN.")
        resp.raise_for_status()
        return str(resp.json()["data"]["version"])

    def _paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            query: dict[str, Any] = {"page": page, "limit": 100}
            if params:
                query.update(params)
            resp = self._send("GET", path, params=query)
            resp.raise_for_status()
            body = resp.json()
            items.extend(body["data"])
            pagination = body.get("meta", {}).get("pagination", {})
            if page >= pagination.get("total_pages", 1):
                break
            page += 1
        return items

    def list_currency_codes(self) -> set[str]:
        """Return the set of currency codes that already exist."""
        return {item["attributes"]["code"] for item in self._paged("currencies")}

    def currency_decimals(self) -> dict[str, int]:
        """Return code -> decimal_places for every currency on the server."""
        return {
            item["attributes"]["code"]: int(item["attributes"]["decimal_places"])
            for item in self._paged("currencies")
        }

    def store_currency(self, payload: dict[str, Any]) -> None:
        """Create a currency."""
        self._post("currencies", payload)

    def store_account(self, payload: dict[str, Any]) -> str:
        """Create an account and return its Firefly id."""
        return str(self._post("accounts", payload)["data"]["id"])

    def store_budget(self, payload: dict[str, Any]) -> str:
        """Create a budget and return its id."""
        return str(self._post("budgets", payload)["data"]["id"])

    def store_budget_limit(self, budget_id: str, payload: dict[str, Any]) -> None:
        """Create a budget limit for ``budget_id``."""
        self._post(f"budgets/{budget_id}/limits", payload)

    def store_bill(self, payload: dict[str, Any]) -> str:
        """Create a bill (subscription) and return its id."""
        return str(self._post("bills", payload)["data"]["id"])

    def bill_index(self) -> dict[str, str]:
        """Return a mapping of existing bill (subscription) name → id."""
        return {item["attributes"]["name"]: str(item["id"]) for item in self._paged("bills")}

    def store_transaction(self, payload: dict[str, Any]) -> str:
        """Create a transaction group; raise DuplicateTransactionError on 422 duplicate.

        Transient errors (429/5xx, connection drops) are retried with backoff.
        Because a gateway error can hide a commit that actually succeeded, each
        retry first checks whether the transaction already landed (by its
        ``external_id``) and returns the existing group id instead of
        re-POSTing a duplicate.
        """
        external_id = next(
            (s.get("external_id") for s in payload.get("transactions", []) if s.get("external_id")),
            None,
        )
        resp: requests.Response | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.post(
                    self._url("transactions"), json=payload, timeout=self._timeout
                )
            except requests.RequestException:
                if attempt == self._max_retries:
                    raise
                self._backoff(attempt, None, "POST transactions")
                landed = self._find_transaction_group(external_id)
                if landed is not None:
                    return landed
                continue
            if _is_transient(resp) and attempt < self._max_retries:
                self._backoff(attempt, resp, "POST transactions")
                landed = self._find_transaction_group(external_id)
                if landed is not None:
                    return landed
                continue
            break
        assert resp is not None  # loop either sets resp, returns, or raises
        if resp.status_code == 422:
            try:
                body = resp.json()
            except Exception:
                body = {}
            errors = body.get("errors", {})
            texts = [msg for messages in errors.values() for msg in messages]
            texts.append(str(body.get("message", "")))
            if any("Duplicate of transaction" in t for t in texts):
                raise DuplicateTransactionError(str(body))
            raise FireflyError(f"POST transactions failed (422): {body}")
        if not resp.ok:
            raise FireflyError(f"POST transactions failed ({resp.status_code}): {resp.text}")
        return str(resp.json()["data"]["id"])

    def _find_transaction_group(self, external_id: str | None) -> str | None:
        """Return the group id of an already-stored transaction, or None.

        Used by the retry path of :meth:`store_transaction`; best-effort, so
        any error is treated as "not found".
        """
        if not external_id:
            return None
        try:
            resp = self._session.get(
                self._url("search/transactions"),
                params={"query": f'external_id_is:"{external_id}"'},
                timeout=self._timeout,
            )
            if not resp.ok:
                return None
            for item in resp.json().get("data", []):
                for split in item.get("attributes", {}).get("transactions", []):
                    if split.get("external_id") == external_id:
                        return str(item["id"])
        except (requests.RequestException, ValueError):
            return None
        return None

    def find_account_id(self, name: str) -> str | None:
        """Return the id of the account exactly named ``name``, or None."""
        resp = self._send(
            "GET", "search/accounts", params={"query": name, "field": "name", "type": "all"}
        )
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if item.get("attributes", {}).get("name") == name:
                return str(item["id"])
        return None

    def account_index(self) -> dict[str, str]:
        """Return a mapping of existing account name → id."""
        return {item["attributes"]["name"]: str(item["id"]) for item in self._paged("accounts")}

    def budget_index(self) -> dict[str, str]:
        """Return a mapping of existing budget name → id."""
        return {item["attributes"]["name"]: str(item["id"]) for item in self._paged("budgets")}

    def existing_external_ids(self, prefix: str = "skrooge:") -> set[str]:
        """Return the set of transaction external_ids already present that start with ``prefix``."""
        found: set[str] = set()
        for item in self._paged("transactions"):
            for split in item.get("attributes", {}).get("transactions", []):
                ext = split.get("external_id")
                if ext and ext.startswith(prefix):
                    found.add(ext)
        return found

    def external_id_to_group_id(self, prefix: str = "skrooge:") -> dict[str, str]:
        """Map each transaction's ``external_id`` (prefixed) to its group id."""
        result: dict[str, str] = {}
        for item in self._paged("transactions"):
            group_id = str(item["id"])
            for split in item.get("attributes", {}).get("transactions", []):
                ext = split.get("external_id")
                if ext and ext.startswith(prefix):
                    result.setdefault(ext, group_id)
        return result

    def account_states(self, account_type: str) -> dict[str, tuple[str, bool]]:
        """Map account name → (id, active) for accounts of ``account_type``."""
        result: dict[str, tuple[str, bool]] = {}
        for item in self._paged("accounts", {"type": account_type}):
            attr = item["attributes"]
            result[attr["name"]] = (str(item["id"]), bool(attr.get("active", True)))
        return result

    def list_accounts(self, account_type: str) -> list[dict[str, Any]]:
        """Return raw account items of ``account_type`` (for export)."""
        return self._paged("accounts", {"type": account_type})

    def list_transaction_groups(self) -> list[dict[str, Any]]:
        """Return all raw transaction-group items (for export)."""
        return self._paged("transactions")

    def list_budgets_with_limits(self) -> list[tuple[str, list[dict[str, Any]]]]:
        """Return (budget name, raw limit items) pairs (for export)."""
        result: list[tuple[str, list[dict[str, Any]]]] = []
        for item in self._paged("budgets"):
            limits = self._paged(f"budgets/{item['id']}/limits")
            result.append((item["attributes"]["name"], limits))
        return result

    def update_transaction(self, group_id: str, payload: dict[str, Any]) -> None:
        """Replace an existing transaction group via PUT."""
        self._put(f"transactions/{group_id}", payload)

    def update_account(self, account_id: str, payload: dict[str, Any]) -> None:
        """Update an existing account via PUT."""
        self._put(f"accounts/{account_id}", payload)

    def account_balances(self, account_type: str) -> dict[str, tuple[str, Decimal, str]]:
        """Return name → (id, balance, currency) for accounts of ``account_type``.

        Balances are taken as of a far-future date so future-dated transactions
        (e.g. recurring instances) are included, matching the IR's totals. The
        date is capped below Firefly's 2038-01-17 (Y2038) upper bound.
        """
        result: dict[str, tuple[str, Decimal, str]] = {}
        for item in self._paged("accounts", {"type": account_type, "date": "2037-12-31"}):
            attr = item["attributes"]
            result[attr["name"]] = (
                str(item["id"]),
                Decimal(str(attr.get("current_balance") or "0")),
                attr.get("currency_code", ""),
            )
        return result

    def transaction_group_count(self) -> int:
        """Return the total number of transaction groups on the server."""
        resp = self._send("GET", "transactions", params={"limit": 1, "page": 1})
        resp.raise_for_status()
        return int(resp.json()["meta"]["pagination"]["total"])

    def account_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Return all transactions in one account's register (paged)."""
        return self._paged(f"accounts/{account_id}/transactions")

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._send("POST", path, json=payload)
        if not resp.ok:
            raise FireflyError(f"POST {path} failed ({resp.status_code}): {resp.text}")
        return resp.json()  # type: ignore[no-any-return]

    def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._send("PUT", path, json=payload)
        if not resp.ok:
            raise FireflyError(f"PUT {path} failed ({resp.status_code}): {resp.text}")
        return resp.json()  # type: ignore[no-any-return]
