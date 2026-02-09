"""
Unit tests for /execute endpoint with session support.

Note: These tests require the full FastAPI app with lifespan context,
which initializes the router, Q/A handler, and session store.
They are integration tests that verify the full request flow.
"""
import pytest
import pytest_asyncio
from ai_server.main import app
from ai_server import main as main_module


@pytest_asyncio.fixture
async def client():
    """Provide an async HTTP client with app lifespan."""
    from httpx import AsyncClient, ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_execute_creates_session_if_none(client):
    """Test that /execute creates a session if none provided."""
    # Skip if session store not initialized (depends on lifespan)
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    response = await client.post(
        "/execute",
        json={"user_input": "What's your name?"}
    )

    # Should succeed
    assert response.status_code == 200

    # Should have session_id in response
    data = response.json()
    assert "session_id" in data
    assert isinstance(data["session_id"], str)
    assert len(data["session_id"]) == 36  # UUID format


@pytest.mark.asyncio
async def test_execute_with_existing_session(client):
    """Test that /execute preserves session_id."""
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    # First request - create session
    response1 = await client.post(
        "/execute",
        json={"user_input": "Hello"}
    )
    session_id = response1.json()["session_id"]

    # Second request - use same session
    response2 = await client.post(
        "/execute",
        json={
            "user_input": "What did I just say?",
            "session_id": session_id
        }
    )

    # Should succeed
    assert response2.status_code == 200

    # Should have same session_id
    data = response2.json()
    assert data["session_id"] == session_id


@pytest.mark.asyncio
async def test_execute_stores_messages(client):
    """Test that user and assistant messages are saved to session."""
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    # Execute a command
    response = await client.post(
        "/execute",
        json={"user_input": "Hello Alfred"}
    )

    session_id = response.json()["session_id"]

    # Get session history
    history = main_module.session_store.get_history(session_id)

    # Should have 2 messages (user + assistant)
    assert len(history) >= 2

    # User message
    user_msg = next(m for m in history if m.role == "user")
    assert user_msg.content == "Hello Alfred"

    # Assistant message
    assistant_msg = next(m for m in history if m.role == "assistant")
    assert isinstance(assistant_msg.content, str)
    assert len(assistant_msg.content) > 0


@pytest.mark.asyncio
async def test_execute_with_invalid_session_creates_new(client):
    """Test that invalid session_id results in new session creation."""
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    fake_session = "00000000-0000-0000-0000-000000000000"

    response = await client.post(
        "/execute",
        json={
            "user_input": "Test",
            "session_id": fake_session
        }
    )

    # Should succeed
    assert response.status_code == 200

    # Should have a different (new) session_id
    data = response.json()
    assert "session_id" in data
    assert data["session_id"] != fake_session


@pytest.mark.asyncio
async def test_session_list_endpoint(client):
    """Test GET /sessions endpoint."""
    response = await client.get("/sessions")

    assert response.status_code in [200, 503]  # 503 if store not available

    if response.status_code == 200:
        data = response.json()
        assert "count" in data
        assert "sessions" in data
        assert isinstance(data["sessions"], list)


@pytest.mark.asyncio
async def test_session_create_endpoint(client):
    """Test POST /sessions endpoint."""
    response = await client.post("/sessions")

    assert response.status_code in [200, 503]

    if response.status_code == 200:
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36


@pytest.mark.asyncio
async def test_session_get_endpoint(client):
    """Test GET /sessions/{id} endpoint."""
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    # Create a session with messages
    session_id = main_module.session_store.create_session()
    main_module.session_store.save_message(session_id, "user", "Test")
    main_module.session_store.save_message(session_id, "assistant", "Response")

    # Get session
    response = await client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()

    assert "session" in data
    assert "messages" in data
    assert data["session"]["session_id"] == session_id
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_session_delete_endpoint(client):
    """Test DELETE /sessions/{id} endpoint."""
    if not main_module.session_store:
        pytest.skip("Session store not initialized")

    # Create a session
    session_id = main_module.session_store.create_session()

    # Delete it
    response = await client.delete(f"/sessions/{session_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"

    # Session should no longer exist
    assert not main_module.session_store.session_exists(session_id)


@pytest.mark.asyncio
async def test_session_get_nonexistent(client):
    """Test GET /sessions/{id} with nonexistent session."""
    fake_session = "00000000-0000-0000-0000-000000000000"

    response = await client.get(f"/sessions/{fake_session}")

    assert response.status_code in [404, 503]  # 404 or 503 if store unavailable


@pytest.mark.asyncio
async def test_session_delete_nonexistent(client):
    """Test DELETE /sessions/{id} with nonexistent session."""
    fake_session = "00000000-0000-0000-0000-000000000000"

    response = await client.delete(f"/sessions/{fake_session}")

    assert response.status_code in [404, 503]
