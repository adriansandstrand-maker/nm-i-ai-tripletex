# Tripletex AI Accounting Agent — Build Instructions

## Overview
Build a FastAPI HTTPS endpoint that receives accounting task prompts and uses the Tripletex API to solve them. Deploy on Vercel or as a local ngrok tunnel for testing.

## Endpoint Spec
- **POST /solve** — receives task, returns `{"status": "completed"}`
- **Timeout:** 300 seconds (5 minutes)
- **Content-Type:** application/json

### Request Format
```json
{
  "task_id": "create_employee_01",
  "prompt": "Opprett en ansatt med navn Ola Nordmann, epost ola@example.com, ...",
  "language": "nb",
  "base_url": "https://proxy-url.ainm.no/tripletex",
  "session_token": "abc123...",
  "attachments": [
    {
      "filename": "invoice.pdf",
      "content_type": "application/pdf",
      "data": "base64-encoded-content"
    }
  ]
}
```

### Response Format
```json
{"status": "completed"}
```

### Tripletex API Auth
- Basic Auth: username=`0`, password=`session_token`
- All API calls go through `base_url` (proxy)
- Example: `GET {base_url}/v2/employee?fields=id,firstName,lastName`

### API Tips
- Use `?fields=*` to see all fields
- POST/PUT take JSON body
- DELETE uses ID in URL path
- List responses: `{"fullResultSize": N, "values": [...]}`
- Use `?from=0&count=100` for pagination

## Task Categories (30 types, 3 tiers)
### Tier 1 (×1 multiplier) — Simple single-API-call tasks
- Create employees, set roles, update contact info
- Register customers, create products
- Create departments, enable modules

### Tier 2 (×2 multiplier) — Multi-step workflows  
- Create invoices (need customer + product first)
- Register payments
- Issue credit notes
- Travel expense reports
- Create projects linked to customers

### Tier 3 (×3 multiplier) — Opens SATURDAY — Complex multi-step
- Delete/reverse incorrect entries
- Complex multi-resource workflows

## Scoring
- **Correctness:** field-by-field verification, normalized to 0-1
- **Tier multiplier:** correctness × tier (1/2/3)
- **Efficiency bonus:** ONLY for perfect (1.0) correctness
  - Fewer write calls (POST/PUT/DELETE/PATCH) = higher bonus
  - Fewer 4xx errors = higher bonus
  - GET calls don't count against you
  - Can up to DOUBLE the tier score
- **Best score per task kept** — bad runs never lower score
- **Total = sum of best scores across all 30 task types**

## Languages
Prompts come in: nb (Norwegian Bokmål), en, es, pt, nn (Nynorsk), de, fr
Agent must handle ALL 7 languages.

## Architecture
1. FastAPI endpoint receives POST /solve
2. Use Claude API (or other LLM) to parse the prompt and determine task type + required data
3. Make Tripletex API calls via the provided proxy base_url
4. Return {"status": "completed"}

## Key Tripletex API Endpoints
- `GET/POST /v2/employee` — employees
- `GET/POST /v2/customer` — customers  
- `GET/POST /v2/product` — products
- `GET/POST /v2/invoice` — invoices
- `GET/POST /v2/order` — orders
- `GET/POST /v2/project` — projects
- `GET/POST /v2/department` — departments
- `GET/POST /v2/travelExpense` — travel expenses
- `GET/POST /v2/ledger/voucher` — vouchers/corrections
- `POST /v2/invoice/{id}/payment` — register payment
- `POST /v2/invoice/{id}/createCreditNote` — credit notes

## Common Patterns
1. **Create Employee:** POST /v2/employee with firstName, lastName, email, etc.
2. **Create Invoice:** First create customer + product, then POST /v2/invoice with lines
3. **Register Payment:** POST /v2/invoice/{id}/payment with amount and date
4. **Delete Entry:** DELETE /v2/resource/{id}

## Efficiency Tips
- Parse prompt FULLY before making any API calls
- Avoid trial-and-error (4xx errors reduce efficiency bonus)
- Use response IDs directly (don't re-fetch what you just created)
- Minimize unnecessary GET calls

## Deploy Options
1. **Vercel** — serverless, 300s timeout on Pro plan (free plan = 60s, too short!)
2. **Google Cloud Run** — Docker container, free with GCP credits
3. **Local + ngrok** — for testing: `ngrok http 8000`

## API Key Protection
If we set an API key when submitting, it's sent as Bearer token:
`Authorization: Bearer <your-api-key>`

## Important Notes
- Each submission gets a BRAND NEW Tripletex account (empty, start from scratch)
- Norwegian characters (æ, ø, å) work fine — send as UTF-8
- Some tasks require enabling modules first (e.g., department accounting)
- Some tasks include PDF attachments — decode base64, extract data
- All API calls through proxy are logged for debugging
