"""
Pytest configuration and shared fixtures for Alfred tests.
"""
import pytest
import tempfile
import os
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport

# Import will be available after we create the memory module
# For now, we'll add imports as we build


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
    """Provide an in-memory SQLite database for testing."""
    return ":memory:"


@pytest.fixture
async def test_app():
    """Provide a test FastAPI app instance."""
    # Import here to avoid circular imports
    from ai_server.main import app
    return app


@pytest.fixture
async def test_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing FastAPI endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test"
    ) as client:
        yield client
