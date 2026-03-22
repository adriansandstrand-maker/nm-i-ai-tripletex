"""Tripletex AI Agent V3 — FastAPI endpoint."""

import json
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="Tripletex AI Accounting Agent V3")
handler = app

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


@app.post("/solve")
async def solve(request: Request):
    raw = await request.json()
    
    prompt = raw.get("prompt", "")
    files = raw.get("files", [])
    creds = raw.get("tripletex_credentials", {})
    base_url = creds.get("base_url", raw.get("base_url", ""))
    session_token = creds.get("session_token", raw.get("session_token", ""))

    logger.info("Task received | base_url=%s | prompt=%s", base_url[:60], prompt[:300])
    logger.info("Files: %d | Language: %s", len(files), raw.get("language", "nb"))
    logger.info("Full raw keys: %s", list(raw.keys()))

    try:
        from agent import solve_task
        await solve_task(
            prompt=prompt,
            language=raw.get("language", "nb"),
            base_url=base_url,
            session_token=session_token,
            attachments=files,
            anthropic_api_key=ANTHROPIC_API_KEY,
        )
    except Exception as e:
        logger.error("Task failed: %s", e)

    return {"status": "completed"}


@app.get("/solve")
async def solve_get():
    return {"status": "ok", "version": "v3"}

@app.post("/")
async def root_solve(request: Request):
    """Mirror /solve at root — competition POSTs here."""
    return await solve(request)

@app.get("/")
async def root():
    return {"status": "ok", "service": "Tripletex AI Agent V3"}
