import asyncio
from contextlib import asynccontextmanager
import logging
import os
from importlib import import_module
from pathlib import Path
import sys

from fastapi import FastAPI

from config.env_loader import load_environment
from integrations.telegram_admin import ensure_telegram_mode, resolve_telegram_mode

load_environment(context="main")

from database import close_db, init_db
from observability import init_observability
from brain.core import validate_runtime_environment
from brain.revenue_scheduler import RevenueBrainScheduler, should_start_revenue_scheduler


def _env_bool(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes"}


async def _maybe_run_db_migrations(logger: logging.Logger) -> None:
    if (
        os.getenv("PYTEST_CURRENT_TEST")
        or any("pytest" in arg for arg in sys.argv)
        or ("pytest" in sys.modules)
    ):
        return
    if not _env_bool("RUN_DB_MIGRATIONS", "false"):
        return
    logger.info("RUN_DB_MIGRATIONS=true: applying Alembic migrations.")
    from tools.db_migrate import run_upgrade

    await asyncio.to_thread(run_upgrade, "head")
    logger.info("Alembic migrations applied.")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    logger = logging.getLogger("kan_core")
    background_tasks: list[asyncio.Task] = []
    jarvis_listener = None
    gui_process: asyncio.subprocess.Process | None = None
    startup_gate = asyncio.Event()
    revenue_scheduler: RevenueBrainScheduler | None = None
    telegram_mode_hint = resolve_telegram_mode()
    resolved_telegram_mode = str(telegram_mode_hint.get("mode") or "off")

    await _maybe_run_db_migrations(logger)

    if os.getenv("AUTO_CREATE_DB", "false").lower() in {"1", "true", "yes"}:
        await init_db()

    # Evita choque webhook/polling antes de arrancar ngrok.
    if resolved_telegram_mode in {"polling", "off"} and _env_bool(
        "ENABLE_NGROK_AUTO_REGISTER_TELEGRAM", "true"
    ):
        os.environ["ENABLE_NGROK_AUTO_REGISTER_TELEGRAM"] = "false"
        logger.info(
            "Telegram mode=%s: deshabilitando auto-registro de webhook en ngrok "
            "(ENABLE_NGROK_AUTO_REGISTER_TELEGRAM=false).",
            resolved_telegram_mode,
        )

    # Auto-gestión de ngrok: el agente levanta su propio túnel
    _ngrok = None
    if os.getenv("ENABLE_NGROK_AUTO", "false").lower() in {"1", "true", "yes"}:
        try:
            from brain.ngrok_manager import get_manager
            _ngrok = get_manager()
            url = await _ngrok.start()
            if url:
                logger.info(
                    "Agente público en: %s", url
                )
        except Exception as exc:
            logger.warning("NgrokManager falló (no crítico): %s", exc)

    in_pytest = bool(os.getenv("PYTEST_CURRENT_TEST") or any("pytest" in arg for arg in sys.argv))
    if not in_pytest:
        telegram_state = await ensure_telegram_mode(logger)
        resolved_telegram_mode = str(telegram_state.get("mode") or resolved_telegram_mode)
        logger.info("Telegram mode resuelto: %s", resolved_telegram_mode)

        validate_runtime_environment(
            context="main_boot",
            require_client_id=False,
            require_openai=False,
            fail_fast=False,
        )

        async def _wait_for_start_gate() -> None:
            await startup_gate.wait()

        if _env_bool("ENABLE_JARVIS_LISTENER", "false"):
            load_environment(
                strict=True,
                required_all=("JARVIS_CLIENT_ID",),
                context="jarvis_listener",
            )
            validate_runtime_environment(
                context="jarvis_listener",
                require_client_id=True,
                require_openai=False,
                strict_client_id_format=_env_bool("STRICT_JARVIS_CLIENT_ID_FORMAT", "false"),
                fail_fast=True,
            )
            from jarvis_listener import JarvisListener

            jarvis_listener = JarvisListener()

            async def _run_listener_when_ready() -> None:
                await _wait_for_start_gate()
                await jarvis_listener.start()

            background_tasks.append(
                asyncio.create_task(
                    _run_listener_when_ready(),
                    name="jarvis-listener",
                )
            )
            logger.info("Jarvis listener task prepared")

        if resolved_telegram_mode == "polling":
            load_environment(
                strict=True,
                required_all=("JARVIS_CLIENT_ID",),
                required_any=(("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),),
                context="telegram_polling",
            )
            validate_runtime_environment(
                context="telegram_polling",
                require_client_id=True,
                require_openai=_env_bool("TELEGRAM_REQUIRE_OPENAI", "false"),
                strict_client_id_format=_env_bool("STRICT_JARVIS_CLIENT_ID_FORMAT", "false"),
                fail_fast=True,
            )
            from workers.telegram_polling import start_polling_task

            async def _run_polling_when_ready() -> None:
                await _wait_for_start_gate()
                polling_task = start_polling_task()
                await polling_task

            background_tasks.append(
                asyncio.create_task(
                    _run_polling_when_ready(),
                    name="telegram-polling-bootstrap",
                )
            )
            logger.info("Telegram polling task prepared")
        else:
            logger.info("Telegram polling omitido (mode=%s)", resolved_telegram_mode)

        if _env_bool("ENABLE_JARVIS_GUI", "false"):
            gui_process = await asyncio.create_subprocess_exec(
                sys.executable,
                "jarvis_gui.py",
                cwd=str(Path(__file__).resolve().parent),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info("Jarvis GUI process started pid=%s", gui_process.pid)

        startup_gate.set()
        if should_start_revenue_scheduler():
            revenue_scheduler = RevenueBrainScheduler()
            revenue_scheduler.start()
            _app.state.revenue_scheduler = revenue_scheduler

    try:
        yield
    finally:
        if revenue_scheduler is not None:
            await revenue_scheduler.shutdown()
        if jarvis_listener is not None:
            try:
                await jarvis_listener.stop()
            except Exception as exc:
                logger.warning("Jarvis listener stop failed: %s", exc)

        active_tasks = [task for task in background_tasks if not task.done()]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        if gui_process is not None and gui_process.returncode is None:
            gui_process.terminate()
            try:
                await asyncio.wait_for(gui_process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                gui_process.kill()
                await gui_process.wait()

        if _ngrok:
            await _ngrok.stop()
        try:
            from brain.runtime_events import runtime_event_bus

            await runtime_event_bus.shutdown()
        except Exception:
            pass
        await close_db()


app = FastAPI(title="kan-core", version="0.1.0", lifespan=_lifespan)
init_observability(app)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _include_router(module_path: str, *, attr: str = "router") -> None:
    if module_path in _INCLUDED_ROUTER_MODULES:
        return
    router_logger = logging.getLogger("kan_core.routes")
    try:
        module = import_module(module_path)
        router = getattr(module, attr, None)
        if router is not None:
            app.include_router(router)
            _INCLUDED_ROUTER_MODULES.add(module_path)
            if module_path in {"routes.ycloud", "routes.mentor"}:
                router_logger.info("Included router %s", module_path)
        elif module_path in {"routes.ycloud", "routes.mentor"}:
            router_logger.error("Router module %s imported but '%s' was not found", module_path, attr)
    except Exception:
        if module_path in {"routes.ycloud", "routes.mentor"}:
            router_logger.exception("Failed to include router %s", module_path)
        # Keep app boot resilient even if optional dependencies are missing.
        return


def _find_duplicate_routes(fastapi_app: FastAPI) -> dict[tuple[str, str], list[str]]:
    registry: dict[tuple[str, str], list[str]] = {}
    for route in fastapi_app.routes:
        path = str(getattr(route, "path", "") or "").strip()
        methods = getattr(route, "methods", None) or set()
        if not path or not methods:
            continue
        endpoint = getattr(route, "endpoint", None)
        endpoint_name = (
            f"{getattr(endpoint, '__module__', '?')}:{getattr(endpoint, '__name__', '?')}"
            if endpoint is not None
            else "unknown"
        )
        for method in sorted(str(m).upper() for m in methods if str(m).upper() not in {"HEAD", "OPTIONS"}):
            key = (method, path)
            registry.setdefault(key, []).append(endpoint_name)
    return {key: value for key, value in registry.items() if len(value) > 1}


def _enforce_route_registry(fastapi_app: FastAPI) -> dict[tuple[str, str], list[str]]:
    duplicates = _find_duplicate_routes(fastapi_app)
    if not duplicates:
        return {}
    logger = logging.getLogger("kan_core")
    for (method, path), endpoints in sorted(duplicates.items()):
        logger.error(
            "Duplicate route detected: %s %s -> %s",
            method,
            path,
            ", ".join(endpoints),
        )
    if _env_bool("STRICT_ROUTE_REGISTRY", "false"):
        raise RuntimeError(
            "Duplicate routes detected and STRICT_ROUTE_REGISTRY=true."
        )
    return duplicates


_INCLUDED_ROUTER_MODULES: set[str] = set()
_CORE_ROUTERS = [
    "routes.webhooks",
    "routes.shopify",
    "routes.chat",
    "routes.mentor",
    "routes.desktop_autonomy",
    "routes.runtime_kernel",
    "routes.runtime_hooks",
]
for _module in _CORE_ROUTERS:
    _include_router(_module)

# Load every router module available under routes/ to keep API surface stable.
if os.getenv("ENABLE_EXTENDED_ROUTES", "true").lower() in {"1", "true", "yes"}:
    routes_dir = Path(__file__).resolve().parent / "routes"
    for path in sorted(routes_dir.glob("*.py")):
        name = path.stem
        if name.startswith("_") or name == "__init__":
            continue
        _include_router(f"routes.{name}")


@app.get("/")
async def root() -> dict:
    return {"status": "ok", "service": "kan-core"}


@app.get("/favicon.ico", status_code=204)
async def favicon() -> None:
    return None


_ROUTE_DUPLICATES = _enforce_route_registry(app)
