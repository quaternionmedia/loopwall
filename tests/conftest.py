import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio's pytest plugin (a FastAPI/Starlette transitive dep) to asyncio -
    trio isn't a project dependency, so the default dual-backend run would fail on it."""
    return "asyncio"
