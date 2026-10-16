from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.db import get_connection, init_db
from app.routers import assumptions, scenarios, solve, tiller


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(get_connection())
    yield


app = FastAPI(title="Owl Retirement Dashboard", lifespan=lifespan)

app.include_router(assumptions.router)
app.include_router(tiller.router)
app.include_router(solve.router)
app.include_router(scenarios.router)

FAVICON_PATH = Path(__file__).resolve().parent / "static" / "favicon.png"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FAVICON_PATH)
