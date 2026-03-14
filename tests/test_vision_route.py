from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import vision.vision_service as vision_service
from database import get_session
from security import require_client_token

TEST_CLIENT_ID = "11111111-1111-1111-1111-111111111111"
PNG_1X1_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2N5v0AAAAASUVORK5CYII="


class DummySession:
    async def execute(self, _stmt: Any) -> Any:
        return None


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    main.app.dependency_overrides = {}
    yield
    main.app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_vision_analyze_returns_coordinates_and_creates_click_command(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def override_session() -> Any:
        yield DummySession()

    def fake_analyze_screen(_image_bytes: bytes, _instruction: str, **_kwargs: Any) -> dict:
        return {
            "element": "login_button",
            "x": 540,
            "y": 320,
            "confidence": 0.92,
            "image_width": 1280,
            "image_height": 720,
            "detections": [
                {"element": "login_button", "x": 540, "y": 320, "confidence": 0.92}
            ],
            "raw_response": '{"element":"login_button","x":540,"y":320,"confidence":0.92}',
        }

    async def fake_send_to_desktop_agent(**_kwargs: Any) -> dict:
        return {"accepted": True, "command_id": "cmd-click-1", "status": "pending_approval"}

    main.app.dependency_overrides[require_client_token] = lambda: TEST_CLIENT_ID
    main.app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(vision_service, "analyze_screen", fake_analyze_screen)
    monkeypatch.setattr(vision_service, "send_to_desktop_agent", fake_send_to_desktop_agent)

    response = client.post(
        "/vision/analyze",
        json={
            "instruction": "Find the login button",
            "image_base64": PNG_1X1_BASE64,
            "auto_click": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["x"] == 540
    assert body["y"] == 320
    assert body["element"] == "login_button"
    assert body["command_created"] is True
    assert body["command_id"] == "cmd-click-1"
