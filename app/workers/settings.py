import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.redis import create_cache_client
from app.workers.tasks.hold_expirer import expire_holds
from app.workers.tasks.outbox_dispatcher import dispatch_outbox

configure_logging(debug=settings.DEBUG)
log = structlog.get_logger()


async def on_startup(ctx: dict) -> None:
    ctx["engine"] = create_async_engine(str(settings.DATABASE_URL), pool_size=5)
    ctx["session"] = async_sessionmaker(ctx["engine"], expire_on_commit=False)
    ctx["redis"] = create_cache_client()
    log.info("worker_started")


async def on_shutdown(ctx: dict) -> None:
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()
    log.info("worker_stopped")


async def ping(ctx: dict) -> str:
    return "pong"


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(str(settings.REDIS_URL))
    functions = [ping, expire_holds, dispatch_outbox]
    cron_jobs = [
        cron(expire_holds, second={0, 10, 20, 30, 40, 50}),
        cron(dispatch_outbox, second={5, 15, 25, 35, 45, 55}),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_jobs = 10
    job_timeout = 120