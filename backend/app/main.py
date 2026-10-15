from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.db import get_connection, init_db

app = FastAPI(title="Owl Retirement Dashboard")

FAVICON_PATH = Path(__file__).resolve().parent / "static" / "favicon.png"


@app.on_event("startup")
def on_startup():
    init_db(get_connection())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FAVICON_PATH)
