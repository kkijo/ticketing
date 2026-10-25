from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from app.api.middleware import RequestContextMiddleware
from app.api.routes.admin_events import router as admin_events_router
from app.api.routes.admin_venues import router as admin_venues_router
from app.api.routes.auth import router as auth_router
from app.api.routes.me import router as me_router
from app.api.routes.orders import router as orders_router
from app.api.routes.showtimes import router as showtimes_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.webhooks import router as webhooks_router
from app.core.config import settings
from app.core.db import engine
from app.core.logging import configure_logging
from app.core.redis import create_arq_pool, create_cache_client

configure_logging(debug=settings.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.arq = await create_arq_pool()
    app.state.cache = create_cache_client()
    try:
        yield
    finally:
        await app.state.cache.aclose()
        await app.state.arq.aclose()
        await engine.dispose()


app = FastAPI(title="Ticketing API", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)

app.include_router(auth_router)
app.include_router(me_router)
app.include_router(admin_venues_router)
app.include_router(admin_events_router)
app.include_router(showtimes_router)
app.include_router(orders_router)
app.include_router(tickets_router)
app.include_router(webhooks_router)

app.mount("/metrics", make_asgi_app())


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}