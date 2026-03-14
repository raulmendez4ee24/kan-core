import os

from fastapi import FastAPI

from database import init_db
from observability import init_observability
from routes.shopify import router as shopify_router
from routes.webhooks import router as webhooks_router

app = FastAPI(title="kan-core", version="0.1.0")
init_observability(app)


@app.on_event("startup")
async def on_startup() -> None:
    if os.getenv("AUTO_CREATE_DB", "false").lower() in {"1", "true", "yes"}:
        await init_db()


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


app.include_router(webhooks_router)
app.include_router(shopify_router)
