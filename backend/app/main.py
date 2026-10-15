from fastapi import FastAPI

app = FastAPI(title="Owl Retirement Dashboard")


@app.get("/health")
def health():
    return {"status": "ok"}
