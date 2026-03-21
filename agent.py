"""LLM-powered agent that parses prompts and executes Tripletex API calls."""

import json
import logging
import base64
import traceback

import anthropic

from tripletex_client import TripletexClient

logger = logging.getLogger("agent")

SYSTEM_PROMPT = """\
You are a Tripletex accounting API automation agent. You receive a task prompt \
(possibly in Norwegian Bokmål, Nynorsk, English, Spanish, Portuguese, German, or French) \
and must determine the exact Tripletex API calls needed to complete the task.

## Tripletex API Reference

Base patterns:
- List: GET /v2/{resource}?fields=*&from=0&count=100
- Create: POST /v2/{resource} with JSON body
- Update: PUT /v2/{resource}/{id} with full JSON body
- Delete: DELETE /v2/{resource}/{id}
- Auth is handled for you — just specify path, method, and body.

Key endpoints:
- /v2/employee — firstName, lastName, email, dateOfBirth (YYYY-MM-DD), etc.
- /v2/customer — name, email, phoneNumber, postalAddress, etc.
- /v2/product — name, number, priceExcludingVat, priceIncludingVat, vatType (id ref), etc.
- /v2/order — customer (id ref), deliveryDate, orderDate, orderLines, etc.
- /v2/order/orderline — order (id ref), product (id ref), count, unitPriceExcludingVat, etc.
- /v2/invoice — create from order: POST /v2/invoice with invoiceDate, order (id ref)
- /v2/invoice/{id}/:payment — paymentDate, paymentType (id ref), amount, amountCurrency
- /v2/invoice/{id}/:createCreditNote — create credit note for invoice
- /v2/project — name, number, projectManager (employee id ref), customer (id ref), etc.
- /v2/department — name, departmentNumber
- /v2/travelExpense — employee (id ref), project (id ref), etc.
- /v2/ledger/voucher — for corrections
- /v2/contact — for contact persons on customers
- /v2/ledger/account — chart of accounts
- /v2/currency — currencies
- /v2/ledger/vatType — VAT types

ID references use format: {"id": 123}

## Address format for customers/employees:
postalAddress: {"addressLine1": "...", "postalCode": "...", "city": "..."}

## Important rules:
1. Parse the prompt completely BEFORE outputting any API calls.
2. Extract ALL required fields from the prompt — names, emails, amounts, dates, etc.
3. For multi-step tasks (e.g., create invoice), output steps in dependency order.
4. Use GET calls to discover existing resources when needed (e.g., find vatType IDs).
5. Minimize write calls — combine where possible.
6. Use response IDs from earlier steps in later steps (referenced as $step_N_id).
7. For dates, use YYYY-MM-DD format.
8. Norwegian characters (æ, ø, å) are fine — use them as-is.
9. When looking up VAT types, GET /v2/ledger/vatType?fields=id,name,number,percentage

## Creating an invoice workflow:
1. Create customer (if not existing): POST /v2/customer
2. Create product (if not existing): POST /v2/product
3. Create order: POST /v2/order with customer ref, deliveryDate, orderDate
4. Add order lines: POST /v2/order/orderline with order ref, product ref, count, unitPriceExcludingVat
5. Create invoice from order: POST /v2/invoice with invoiceDate, order ref
   OR: POST /v2/order/{id}/:invoice with invoiceDate

## Output format:
Return a JSON array of steps. Each step has:
- "method": "GET" | "POST" | "PUT" | "DELETE"
- "path": the API path (e.g., "/v2/employee")
- "params": query params for GET (optional)
- "body": JSON body for POST/PUT (optional)
- "description": brief description of what this step does
- "save_as": variable name to save the response ID as (optional, e.g., "customer_id")
- "save_field": dot-path to extract from response (default: "value.id")
- "depends_on": list of save_as names this step needs (optional)

For steps that depend on earlier steps, use {{variable_name}} in path or body values.

Example: create employee
```json
[
  {
    "method": "POST",
    "path": "/v2/employee",
    "body": {"firstName": "Ola", "lastName": "Nordmann", "email": "ola@example.com"},
    "description": "Create employee Ola Nordmann",
    "save_as": "employee_id"
  }
]
```

Example: create invoice for new customer
```json
[
  {
    "method": "GET",
    "path": "/v2/ledger/vatType",
    "params": {"fields": "id,name,number,percentage", "count": 100},
    "description": "Get VAT types to find correct ID",
    "save_as": "vat_types",
    "save_field": "values"
  },
  {
    "method": "POST",
    "path": "/v2/customer",
    "body": {"name": "Acme AS", "email": "acme@example.com"},
    "description": "Create customer",
    "save_as": "customer_id"
  },
  {
    "method": "POST",
    "path": "/v2/product",
    "body": {"name": "Consulting", "priceExcludingVat": 1000, "vatType": {"id": "{{vat_type_25}}"}},
    "description": "Create product",
    "save_as": "product_id"
  },
  {
    "method": "POST",
    "path": "/v2/order",
    "body": {"customer": {"id": "{{customer_id}}"}, "deliveryDate": "2024-01-15", "orderDate": "2024-01-15"},
    "description": "Create order",
    "save_as": "order_id"
  },
  {
    "method": "POST",
    "path": "/v2/order/orderline",
    "body": {"order": {"id": "{{order_id}}"}, "product": {"id": "{{product_id}}"}, "count": 1},
    "description": "Add order line"
  },
  {
    "method": "POST",
    "path": "/v2/order/{{order_id}}/:invoice",
    "body": {"invoiceDate": "2024-01-15"},
    "description": "Create invoice from order",
    "save_as": "invoice_id"
  }
]
```

RESPOND WITH ONLY THE JSON ARRAY. No markdown, no explanation — just valid JSON.
"""


def _build_messages(prompt: str, language: str, attachments: list[dict] | None) -> list[dict]:
    """Build the message list for Claude, including any attachments."""
    content_parts = []

    # Add attachments as images/documents for Claude to extract data from
    if attachments:
        for att in attachments:
            raw_data = att.get("data", "")
            content_type = att.get("content_type", "application/octet-stream")
            filename = att.get("filename", "attachment")

            if content_type.startswith("image/"):
                content_parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": content_type,
                        "data": raw_data,
                    },
                })
            elif content_type == "application/pdf":
                content_parts.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": raw_data,
                    },
                })
            else:
                # Try to decode as text
                try:
                    text = base64.b64decode(raw_data).decode("utf-8")
                    content_parts.append({
                        "type": "text",
                        "text": f"[Attachment: {filename}]\n{text}",
                    })
                except Exception:
                    content_parts.append({
                        "type": "text",
                        "text": f"[Binary attachment: {filename}, type: {content_type}]",
                    })

    content_parts.append({
        "type": "text",
        "text": f"Language: {language}\n\nTask:\n{prompt}",
    })

    return [{"role": "user", "content": content_parts}]


def _resolve_template(value, variables: dict):
    """Recursively resolve {{variable}} placeholders in strings, dicts, and lists."""
    if isinstance(value, str):
        for var_name, var_val in variables.items():
            placeholder = "{{" + var_name + "}}"
            if placeholder in value:
                # If the entire string is just the placeholder, return the raw value (preserving type)
                if value == placeholder:
                    return var_val
                value = value.replace(placeholder, str(var_val))
        return value
    elif isinstance(value, dict):
        return {k: _resolve_template(v, variables) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_template(item, variables) for item in value]
    return value


def _extract_field(data: dict, field_path: str):
    """Extract a value from nested dict using dot-path notation."""
    parts = field_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


async def solve_task(
    prompt: str,
    language: str,
    base_url: str,
    session_token: str,
    attachments: list[dict] | None = None,
    anthropic_api_key: str | None = None,
) -> dict:
    """Main entry point: parse the prompt with Claude, then execute the plan."""
    client = anthropic.Anthropic(api_key=anthropic_api_key)
    tripletex = TripletexClient(base_url, session_token)

    try:
        # Step 1: Ask Claude to create an execution plan
        messages = _build_messages(prompt, language, attachments)

        logger.info("Sending prompt to Claude for planning...")
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        plan_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if plan_text.startswith("```"):
            lines = plan_text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            plan_text = "\n".join(lines)

        try:
            steps = json.loads(plan_text)
        except json.JSONDecodeError:
            logger.error("Failed to parse Claude's plan as JSON: %s", plan_text[:500])
            # Retry with explicit instruction
            messages.append({"role": "assistant", "content": plan_text})
            messages.append({"role": "user", "content": "That was not valid JSON. Please respond with ONLY a valid JSON array of steps, no markdown."})
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            plan_text = response.content[0].text.strip()
            if plan_text.startswith("```"):
                lines = plan_text.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                plan_text = "\n".join(lines)
            steps = json.loads(plan_text)

        if not isinstance(steps, list):
            steps = [steps]

        logger.info("Plan has %d steps", len(steps))

        # Step 2: Execute the plan
        variables: dict[str, any] = {}
        results: list[dict] = []

        for i, step in enumerate(steps):
            method = step.get("method", "GET").upper()
            path = _resolve_template(step.get("path", ""), variables)
            params = _resolve_template(step.get("params"), variables)
            body = _resolve_template(step.get("body"), variables)
            description = step.get("description", f"Step {i+1}")
            save_as = step.get("save_as")
            save_field = step.get("save_field", "value.id")

            logger.info("Step %d/%d: %s — %s %s", i + 1, len(steps), description, method, path)

            try:
                if method == "GET":
                    result = await tripletex.get(path, params)
                elif method == "POST":
                    result = await tripletex.post(path, body)
                elif method == "PUT":
                    result = await tripletex.put(path, body)
                elif method == "DELETE":
                    result = await tripletex.delete(path)
                    result = result or {}
                else:
                    logger.warning("Unknown method: %s", method)
                    continue

                results.append({"step": i + 1, "description": description, "status": "ok"})

                # Save variable if requested
                if save_as and result:
                    extracted = _extract_field(result, save_field)
                    if extracted is not None:
                        variables[save_as] = extracted
                        logger.info("  Saved %s = %s", save_as, str(extracted)[:200])

                        # Special handling: if we saved vat_types (a list), also create
                        # convenience variables like vat_type_25 for 25% VAT
                        if save_as == "vat_types" and isinstance(extracted, list):
                            for vt in extracted:
                                if isinstance(vt, dict) and "id" in vt:
                                    pct = vt.get("percentage") or vt.get("number")
                                    if pct is not None:
                                        variables[f"vat_type_{int(pct)}"] = vt["id"]

            except Exception as e:
                error_msg = str(e)
                logger.error("  Step %d failed: %s", i + 1, error_msg)
                results.append({"step": i + 1, "description": description, "status": "error", "error": error_msg})

                # For critical failures on write operations, ask Claude for a fix
                if method in ("POST", "PUT") and "4" in error_msg[:4]:
                    fix_steps = await _ask_claude_for_fix(
                        client, prompt, language, steps, i, error_msg, variables, results
                    )
                    if fix_steps:
                        # Insert fix steps right after current step
                        for j, fix_step in enumerate(fix_steps):
                            steps.insert(i + 1 + j, fix_step)
                        logger.info("  Claude suggested %d fix steps", len(fix_steps))

        return {"status": "completed", "steps_executed": len(results)}

    finally:
        await tripletex.close()


async def _ask_claude_for_fix(
    client: anthropic.Anthropic,
    original_prompt: str,
    language: str,
    steps: list[dict],
    failed_step_idx: int,
    error_msg: str,
    variables: dict,
    results: list[dict],
) -> list[dict] | None:
    """Ask Claude to fix a failed step."""
    try:
        fix_prompt = f"""The following API call failed. Suggest replacement steps (JSON array).

Original task: {original_prompt}
Language: {language}

Failed step ({failed_step_idx + 1}): {json.dumps(steps[failed_step_idx])}
Error: {error_msg}

Variables so far: {json.dumps({k: v for k, v in variables.items() if not isinstance(v, list)}, default=str)}

Previous results: {json.dumps(results[-3:], default=str)}

Remaining steps: {json.dumps(steps[failed_step_idx + 1:], default=str)}

Return a JSON array of replacement steps for the failed step (and any prerequisite steps needed).
Use {{variable}} syntax for saved variables. Return ONLY valid JSON, no markdown."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": fix_prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            lines = text.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        fix_steps = json.loads(text)
        if isinstance(fix_steps, dict):
            fix_steps = [fix_steps]
        return fix_steps
    except Exception as e:
        logger.error("Failed to get fix from Claude: %s", e)
        return None
