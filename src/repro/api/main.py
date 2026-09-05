"""FastAPI surface. OWNER: Engineer D."""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Repro", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
