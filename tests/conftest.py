import asyncio
import pathlib
import sys
import warnings

import pytest
from database import engine


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text not in sys.path:
    sys.path.insert(0, project_root_text)


warnings.filterwarnings(
    "ignore",
    message=r".*Connection\._cancel.*was never awaited.*",
    category=RuntimeWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"coroutine 'Connection\._cancel' was never awaited",
    category=RuntimeWarning,
)


@pytest.fixture(autouse=True)
def ensure_event_loop():
    """Python 3.13: keep a default loop available for legacy get_event_loop tests."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(loop.shutdown_default_executor())
    asyncio.set_event_loop(None)
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def dispose_db_engine_on_session_end():
    yield
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(engine.dispose())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()
