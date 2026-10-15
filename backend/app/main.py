from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI(title="Owl Retirement Dashboard")

FAVICON_PATH = Path(__file__).resolve().parent / "static" / "favicon.png"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FAVICON_PATH)
