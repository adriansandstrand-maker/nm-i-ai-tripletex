"""Tripletex AI Agent V4 — Agentic loop with tool use."""

import json
import logging
import base64
import datetime
import traceback

import anthropic
import httpx

logger = logging.getLogger("agent")

TODAY = datetime.date.today().isoformat()

SYSTEM_PROMPT = f"""\
You are a Tripletex accounting API agent. Today is {TODAY}.
You have access to a tool `tripletex_api` to call the Tripletex REST API.
Plan your approach, then make API calls one at a time.

## RULES
1. Parse the ENTIRE task prompt first. Extract ALL values (names, emails, amounts, dates, org numbers).
2. Search before creating — the sandbox may have pre-loaded data.
3. Minimize write calls (POST/PUT/DELETE). GET calls are free.
4. Dates: YYYY-MM-DD format. Use {TODAY} if not specified.
5. Return final message "DONE" when complete.

## API REFERENCE (EXACT field names from Swagger)

Response format:
- Single entity: {{"value": {{"id": 123, ...}}}}
- List: {{"fullResultSize": N, "values": [...]}}

### POST /v2/employee — Required: firstName, lastName. Optional: email, dateOfBirth, phoneNumberMobile

### POST /v2/customer — Required: name. Optional: email, phoneNumber, organizationNumber, isCustomer (true)

### POST /v2/supplier — Required: name. Optional: email, organizationNumber, isSupplier (true)

### POST /v2/product — Required: name
PRICE FIELDS (exact names — NOT priceExcludingVat):
- priceExcludingVatCurrency (selling price ex VAT)
- priceIncludingVatCurrency (selling price incl VAT)
- costExcludingVatCurrency (purchase cost ex VAT)
Optional: number (string!), vatType ({{"id": N}})

### POST /v2/order — Required: customer ({{"id": N}}), deliveryDate, orderDate

### POST /v2/order/orderline — Required: order ({{"id": N}}), product ({{"id": N}}), count
Optional: unitPriceExcludingVatCurrency (overrides product price)

### PUT /v2/order/ORDER_ID/:invoice — Creates invoice from order
⚠️ ALL PARAMETERS ARE QUERY PARAMS, NOT BODY:
  params: {{"invoiceDate": "{TODAY}"}}
  body: {{}} (empty or omit)
  Returns the created invoice with id.

### POST /v2/invoice — Direct creation (body): invoiceDate, invoiceDueDate, orders ([{{"id": N}}])

### PUT /v2/invoice/INVOICE_ID/:payment — Register payment on invoice  
⚠️ ALL PARAMETERS ARE QUERY PARAMS, NOT BODY:
  params: {{"paymentDate": "{TODAY}", "paymentTypeId": 1, "paidAmount": 10000}}
  body: {{}} (empty or omit)

### PUT /v2/invoice/INVOICE_ID/:createCreditNote — Create credit note
⚠️ ALL PARAMETERS ARE QUERY PARAMS, NOT BODY:
  params: {{"date": "{TODAY}", "comment": "Credit note"}}
  body: {{}} (empty or omit)

### GET /v2/invoice — REQUIRES invoiceDateFrom AND invoiceDateTo params

### POST /v2/project — Required: name, number (string), projectManager ({{"id": N}})

### POST /v2/department — Required: name, departmentNumber (integer)

### POST /v2/travelExpense — Required: employee ({{"id": N}}), title, isCompleted (false)

### POST /v2/travelExpense — Create travel expense
Required body: employee ({{"id": N}})
Optional: title, date ("{TODAY}"), isCompleted (false), department, project
### DELETE /v2/travelExpense/ID — Delete travel expense

### POST /v2/employee/employment — Create employment for employee  
Required body: startDate ("{TODAY}"), employee ({{"id": N}})
Optional: endDate, employmentId, division, taxDeductionCode

### POST /v2/salary/transaction — Process salary/payroll
Required body: month (1-12), year (2026), payslips (array)
Each payslip needs: employee ({{"id": N}}), date, specification (salary type refs)

### PUT /v2/employee/ID — update employee
### PUT /v2/customer/ID — update customer

### GET /v2/ledger/vatType?fields=id,name,number,percentage&count=100
Standard Norwegian VAT IDs: 3 (25% output), 5 (0% exempt), 33 (15% food)

### GET /v2/ledger/paymentType?fields=id,description&count=100

## SEARCH endpoints:
- GET /v2/employee?firstName=X&lastName=Y&fields=id,firstName,lastName,email
- GET /v2/customer?name=X&fields=id,name,organizationNumber
- GET /v2/customer?organizationNumber=X&fields=id,name
- GET /v2/supplier?name=X&fields=id,name,organizationNumber
- GET /v2/product?name=X&fields=id,name,number
- GET /v2/product?number=X&fields=id,name,number
- GET /v2/department?name=X&fields=id,name,departmentNumber
- GET /v2/project?name=X&fields=id,name,number

## INVOICE WORKFLOW (exact order):
1. Find/create customer
2. Find/create product(s) — use priceExcludingVatCurrency for price!
3. Create order: POST /v2/order (body: customer, deliveryDate, orderDate)
4. Add order lines: POST /v2/order/orderline (body: order, product, count)
5. Invoice from order: PUT /v2/order/ORDER_ID/:invoice 
   → params={{"invoiceDate": "{TODAY}"}}, body={{}}
   → Returns invoice data. Extract invoice ID from response.
6. Payment: PUT /v2/invoice/INVOICE_ID/:payment
   → params={{"paymentDate": "{TODAY}", "paymentTypeId": ID, "paidAmount": AMOUNT}}, body={{}}
7. Credit note: PUT /v2/invoice/INVOICE_ID/:createCreditNote
   → params={{"date": "{TODAY}"}}, body={{}}

## CRITICAL RULES
- ID refs in body: {{"id": 123}} (object), never raw int
- Product number is STRING
- /:invoice, /:payment, /:createCreditNote use QUERY PARAMS not body!
- PUT for action endpoints (/:invoice, /:payment, /:createCreditNote)
- POST for creating entities (employee, customer, product, order, orderline)
- GET /v2/invoice REQUIRES invoiceDateFrom AND invoiceDateTo query params

## SUPPLIER INVOICE
There is NO POST /v2/supplierInvoice — supplier invoices can't be created via API directly.
To register a supplier invoice, create a voucher with appropriate postings:
- Debit: expense account (e.g., 6300 for consulting, 4000 for goods)
- Credit: accounts payable (2400)
Include supplier reference, invoice number, and VAT if applicable.

## VOUCHER/POSTING
POST /v2/ledger/voucher — Create voucher with postings
Body: {{"date": "{TODAY}", "description": "...", "postings": [
  {{"account": {{"id": DEBIT_ACCT_ID}}, "amount": AMOUNT, "amountCurrency": AMOUNT}},
  {{"account": {{"id": CREDIT_ACCT_ID}}, "amount": -AMOUNT, "amountCurrency": -AMOUNT}}
]}}
Posting fields: account, amount, amountCurrency, description, department, project, vatType
To find account IDs: GET /v2/ledger/account?number=ACCT_NUM&fields=id,number,name
"""

TOOLS = [{
    "name": "tripletex_api",
    "description": "Call the Tripletex REST API. Returns the JSON response.",
    "input_schema": {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"]},
            "path": {"type": "string", "description": "API path starting with /v2/"},
            "params": {"type": "object", "description": "Query parameters (for GET and action endpoints like /:invoice, /:payment)"},
            "body": {"type": "object", "description": "JSON body for POST/PUT entity creation"},
        },
        "required": ["method", "path"]
    }
}]


async def _call_api(client, base_url, method, path, params=None, body=None):
    """Make API call and return JSON response or error dict."""
    url = f"{base_url}{path}" if path.startswith("/") else f"{base_url}/{path}"
    try:
        if method == "GET":
            resp = await client.get(url, params=params)
        elif method == "POST":
            resp = await client.post(url, json=body)
        elif method == "PUT":
            if params and (not body or body == {}):
                # Action endpoints (/:invoice, /:payment, etc) use query params
                resp = await client.put(url, params=params)
            elif params and body:
                resp = await client.put(url, params=params, json=body)
            else:
                resp = await client.put(url, json=body)
        elif method == "DELETE":
            resp = await client.delete(url)
        else:
            return {"error": f"Unknown method {method}"}

        logger.info("  API %s %s → %s", method, path, resp.status_code)

        if resp.status_code >= 400:
            error_text = resp.text[:1000]
            logger.error("  Error: %s", error_text[:200])
            return {"error": f"HTTP {resp.status_code}", "details": error_text}

        if resp.status_code == 204 or not resp.content:
            return {"success": True, "status": resp.status_code}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def _build_user_content(prompt, language, attachments):
    parts = []
    if attachments:
        for att in attachments:
            raw_data = att.get("content_base64") or att.get("data", "")
            mime = att.get("mime_type") or att.get("content_type", "application/octet-stream")
            fname = att.get("filename", "file")
            if mime.startswith("image/"):
                parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": raw_data}
                })
            else:
                try:
                    decoded = base64.b64decode(raw_data)
                    text_content = decoded.decode("utf-8", errors="ignore")[:5000]
                    if text_content.strip():
                        parts.append({"type": "text", "text": f"[File: {fname}]\n{text_content}"})
                except Exception:
                    parts.append({"type": "text", "text": f"[Attached: {fname} ({mime})]"})

    parts.append({"type": "text", "text": f"Task ({language}):\n{prompt}"})
    return parts


async def solve_task(prompt, language, base_url, session_token, attachments=None, anthropic_api_key=None):
    """Main entry point — agentic loop with tool use."""
    # Strip /v2 from base_url to avoid doubling
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v2"):
        base_url = base_url[:-3]

    client = httpx.AsyncClient(
        auth=httpx.BasicAuth("0", session_token),
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"Content-Type": "application/json"},
    )
    claude = anthropic.Anthropic(api_key=anthropic_api_key)

    try:
        messages = [{"role": "user", "content": _build_user_content(prompt, language, attachments)}]

        # Agentic loop — max 25 iterations
        for iteration in range(25):
            response = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS,
            )

            # Process response blocks
            tool_calls = []
            text_parts = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if text_parts:
                logger.info("  Claude [%d]: %s", iteration, " ".join(text_parts)[:150])

            # If no tool calls, we're done
            if not tool_calls:
                logger.info("  Agent done after %d iterations", iteration + 1)
                break

            # Add assistant message
            messages.append({"role": "assistant", "content": response.content})

            # Execute tool calls and build results
            tool_results = []
            for tc in tool_calls:
                inp = tc.input
                method = inp.get("method", "GET")
                path = inp.get("path", "")
                params = inp.get("params")
                body = inp.get("body")

                logger.info("  Tool call [%d]: %s %s", iteration, method, path)

                result = await _call_api(client, base_url, method, path, params, body)

                # Truncate large responses
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 4000:
                    # For large list responses, just keep first 5 items
                    if isinstance(result, dict) and "values" in result:
                        result["values"] = result["values"][:5]
                        result["_truncated"] = True
                    result_str = json.dumps(result, ensure_ascii=False)[:4000]

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})

        return {"status": "completed"}

    except Exception as e:
        logger.error("Agent error: %s\n%s", e, traceback.format_exc())
        return {"status": "completed"}
    finally:
        await client.aclose()
