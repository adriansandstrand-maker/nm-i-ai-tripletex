"""Tripletex API client wrapper with common endpoints and retry logic."""

import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

logger = logging.getLogger("tripletex")


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))


class TripletexClient:
    def __init__(self, base_url: str, session_token: str):
        # base_url may already include /v2 — normalize it
        self.base_url = base_url.rstrip("/")
        # If base_url already ends with /v2, paths like /v2/employee would double up
        # So we strip /v2 suffix and always add it in paths
        if self.base_url.endswith("/v2"):
            self.base_url = self.base_url[:-3]
        self.client = httpx.AsyncClient(
            auth=httpx.BasicAuth(username="0", password=session_token),
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"Content-Type": "application/json"},
        )

    async def close(self):
        await self.client.aclose()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _log_error_response(self, method: str, path: str, resp: httpx.Response) -> None:
        """Log response body for all failed requests."""
        try:
            body = resp.text[:1000]
        except Exception:
            body = "<could not read body>"
        logger.error("%s %s → %s: %s", method, path, resp.status_code, body)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def get(self, path: str, params: dict | None = None) -> dict:
        resp = await self.client.get(self._url(path), params=params)
        if resp.status_code == 422 or resp.status_code == 400:
            # For validation errors on GET (e.g., missing required query params),
            # log the error and return empty result instead of raising
            self._log_error_response("GET", path, resp)
            return {"values": [], "fullResultSize": 0}
        if resp.status_code >= 400:
            self._log_error_response("GET", path, resp)
        resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def post(self, path: str, json_data: dict | list | None = None) -> dict:
        logger.info("POST %s body=%s", self._url(path), str(json_data)[:500])
        resp = await self.client.post(self._url(path), json=json_data)
        if resp.status_code >= 400:
            self._log_error_response("POST", path, resp)
        resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def put(self, path: str, json_data: dict | None = None) -> dict:
        logger.info("PUT %s body=%s", self._url(path), str(json_data)[:500])
        resp = await self.client.put(self._url(path), json=json_data)
        if resp.status_code >= 400:
            self._log_error_response("PUT", path, resp)
        resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )
    async def delete(self, path: str) -> dict | None:
        resp = await self.client.delete(self._url(path))
        if resp.status_code >= 400:
            self._log_error_response("DELETE", path, resp)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ── Convenience helpers ──────────────────────────────────────────

    async def list_all(self, path: str, params: dict | None = None, fields: str = "*") -> list[dict]:
        params = dict(params or {})
        params.setdefault("fields", fields)
        params.setdefault("from", 0)
        params.setdefault("count", 1000)
        data = await self.get(path, params)
        return data.get("values", [])

    # Employees
    async def list_employees(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/employee", params=kwargs)

    async def create_employee(self, data: dict) -> dict:
        return await self.post("/v2/employee", data)

    async def update_employee(self, employee_id: int, data: dict) -> dict:
        return await self.put(f"/v2/employee/{employee_id}", data)

    # Customers
    async def list_customers(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/customer", params=kwargs)

    async def create_customer(self, data: dict) -> dict:
        return await self.post("/v2/customer", data)

    async def update_customer(self, customer_id: int, data: dict) -> dict:
        return await self.put(f"/v2/customer/{customer_id}", data)

    # Products
    async def list_products(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/product", params=kwargs)

    async def create_product(self, data: dict) -> dict:
        return await self.post("/v2/product", data)

    # Invoices
    async def list_invoices(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/invoice", params=kwargs)

    async def create_invoice(self, data: dict) -> dict:
        return await self.post("/v2/invoice", data)

    async def register_payment(self, invoice_id: int, data: dict) -> dict:
        return await self.post(f"/v2/invoice/{invoice_id}/:payment", data)

    async def create_credit_note(self, invoice_id: int, data: dict | None = None) -> dict:
        return await self.post(f"/v2/invoice/{invoice_id}/:createCreditNote", data or {})

    # Orders
    async def list_orders(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/order", params=kwargs)

    async def create_order(self, data: dict) -> dict:
        return await self.post("/v2/order", data)

    async def create_orderline(self, data: dict) -> dict:
        return await self.post("/v2/order/orderline", data)

    # Projects
    async def list_projects(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/project", params=kwargs)

    async def create_project(self, data: dict) -> dict:
        return await self.post("/v2/project", data)

    # Departments
    async def list_departments(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/department", params=kwargs)

    async def create_department(self, data: dict) -> dict:
        return await self.post("/v2/department", data)

    # Travel expenses
    async def list_travel_expenses(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/travelExpense", params=kwargs)

    async def create_travel_expense(self, data: dict) -> dict:
        return await self.post("/v2/travelExpense", data)

    # Vouchers / ledger
    async def list_vouchers(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/ledger/voucher", params=kwargs)

    async def create_voucher(self, data: dict) -> dict:
        return await self.post("/v2/ledger/voucher", data)

    # Contacts
    async def create_contact(self, data: dict) -> dict:
        return await self.post("/v2/contact", data)

    # Activities
    async def list_activities(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/activity", params=kwargs)

    # Accounts
    async def list_accounts(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/ledger/account", params=kwargs)

    # Currency
    async def list_currencies(self, **kwargs) -> list[dict]:
        return await self.list_all("/v2/currency", params=kwargs)

    # Generic - for any endpoint the agent needs
    async def api_get(self, path: str, params: dict | None = None) -> dict:
        return await self.get(path, params)

    async def api_post(self, path: str, data: dict | list | None = None) -> dict:
        return await self.post(path, data)

    async def api_put(self, path: str, data: dict | None = None) -> dict:
        return await self.put(path, data)

    async def api_delete(self, path: str) -> dict | None:
        return await self.delete(path)
