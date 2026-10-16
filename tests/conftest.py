"""
Pytest configuration and shared fixtures for Alfred tests.
"""
import pytest
import pytest_asyncio
import tempfile
import os
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport


def pytest_configure(config):
    """Register the ``live`` marker: tests that hit real Jev/Anthropic/HA."""
    config.addinivalue_line(
        "markers",
        "live: hits real services (Jev, Anthropic, Home Assistant); runs only with ALFRED_LIVE=1",
    )


def pytest_collection_modifyitems(config, items):
    """Skip ``live`` tests unless ALFRED_LIVE=1 (omni-jev plan section 3)."""
    if os.environ.get("ALFRED_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: set ALFRED_LIVE=1 to run")
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)


@pytest.fixture
def temp_db_path():
    """Provide a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def in_memory_db():
    """
    Provide a temp file-based database for testing.

    Note: We use a temp file instead of ":memory:" because each
    connection to ":memory:" creates a separate database, which
    doesn't work with SessionStore's connection-per-operation design.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture
async def test_app():
    """Provide a test FastAPI app instance."""
    # Import here to avoid circular imports
    from ai_server.main import app
    return app


@pytest_asyncio.fixture
async def test_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing FastAPI endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test"
    ) as client:
        yield client
