import asyncio
from typing import Any, Dict, List

import pytest

import brain.dispatcher as dispatcher


class FakeEngine:
    def __init__(self, response: Dict[str, Any]) -> None:
        self._response = response
        self.calls: List[Dict[str, str]] = []

    async def generate_response(self, client_id: str, session_id: str, message: str) -> Dict[str, Any]:
        self.calls.append(
            {"client_id": client_id, "session_id": session_id, "message": message}
        )
        return self._response


def test_load_meta_page_client_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", '{"p1":"c1"}')
    assert dispatcher._load_meta_page_client_map() == {"p1": "c1"}

    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", "not-json")
    assert dispatcher._load_meta_page_client_map() == {}

    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", "[1,2,3]")
    assert dispatcher._load_meta_page_client_map() == {}


def test_extract_meta_text_messages() -> None:
    payload = {
        "entry": [
            {
                "id": "page-1",
                "messaging": [
                    {"sender": {"id": "u1"}, "message": {"text": "hola"}},
                ],
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "phone-1"},
                            "messages": [{"from": "u2", "text": {"body": "hello"}}],
                        }
                    }
                ],
            }
        ]
    }
    messages = dispatcher._extract_meta_text_messages(payload)
    assert {m["sender_id"] for m in messages} == {"u1", "u2"}


def test_build_action_policy() -> None:
    policy = dispatcher._build_action_policy({"type": "message", "content": "x"})
    assert policy["requires_human_approval"] is False

    action_policy = dispatcher._build_action_policy(
        {
            "type": "action",
            "payload": {
                "contract_version": "1.0",
                "action": "create_order",
                "integration": "shopify",
                "arguments": {"sku": "A1"},
                "rationale": "requested by user",
            },
        }
    )
    assert action_policy["contract_valid"] is True
    assert action_policy["requires_human_approval"] is True


def test_dispatch_meta_ai_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_META_AI", "false")

    calls: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    asyncio.run(dispatcher._dispatch_meta_ai({"entry": []}))
    assert calls == []


def test_dispatch_meta_ai_no_page_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_META_AI", "true")
    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", "{}")

    sent: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    asyncio.run(dispatcher._dispatch_meta_ai({"entry": [{"id": "page-1"}]}))
    assert sent == []


def test_dispatch_meta_ai_engine_init_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_META_AI", "true")
    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", '{"page-1":"client-1"}')

    captured: List[Dict[str, Any]] = []

    def _raise() -> Any:
        raise RuntimeError("engine failed")

    def fake_capture(exc: Exception, context: Dict[str, Any]) -> None:
        captured.append({"exc": str(exc), "context": context})

    monkeypatch.setattr(dispatcher.CognitiveEngine, "get_instance", _raise)
    monkeypatch.setattr(dispatcher, "capture_exception", fake_capture)
    asyncio.run(dispatcher._dispatch_meta_ai({"entry": []}))

    assert captured and captured[0]["context"]["stage"] == "init_engine"


def test_dispatch_meta_ai_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_META_AI", "true")
    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", '{"page-1":"client-1"}')
    engine = FakeEngine({"type": "message", "content": "hola"})

    sent: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher.CognitiveEngine, "get_instance", lambda: engine)
    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)

    payload = {
        "entry": [
            {
                "id": "page-1",
                "messaging": [{"sender": {"id": "u1"}, "message": {"text": "hola"}}],
            }
        ]
    }
    asyncio.run(dispatcher._dispatch_meta_ai(payload))

    assert engine.calls == [{"client_id": "client-1", "session_id": "meta:u1", "message": "hola"}]
    assert sent and sent[0]["source"] == "meta_ai"


def test_dispatch_meta_ai_handles_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_META_AI", "true")
    monkeypatch.setenv("META_PAGE_CLIENT_MAP_JSON", '{"page-1":"client-1"}')

    class BrokenEngine:
        async def generate_response(self, client_id: str, session_id: str, message: str) -> Dict[str, Any]:
            raise RuntimeError("llm failed")

    captured: List[Dict[str, Any]] = []

    def fake_capture(exc: Exception, context: Dict[str, Any]) -> None:
        captured.append({"exc": str(exc), "context": context})

    async def fake_send(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher.CognitiveEngine, "get_instance", lambda: BrokenEngine())
    monkeypatch.setattr(dispatcher, "capture_exception", fake_capture)
    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)

    payload = {
        "entry": [
            {
                "id": "page-1",
                "messaging": [{"sender": {"id": "u1"}, "message": {"text": "hola"}}],
            }
        ]
    }
    asyncio.run(dispatcher._dispatch_meta_ai(payload))
    assert captured and captured[0]["context"]["stage"] == "generate"


def test_dispatch_client_ai_success(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(
        {
            "type": "action",
            "payload": {
                "contract_version": "1.0",
                "action": "create_order",
                "integration": "shopify",
                "arguments": {"sku": "A1"},
                "rationale": "requested",
            },
        }
    )

    sent: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher.CognitiveEngine, "get_instance", lambda: engine)
    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)

    asyncio.run(
        dispatcher._dispatch_client_ai(
            "client-1",
            {"message": "crea pedido", "session_id": "s1", "metadata": {"tenant": "acme"}},
        )
    )

    assert sent and sent[0]["source"] == "client_ai"
    assert sent[0]["action_policy"]["requires_human_approval"] is True


def test_dispatch_client_ai_ignores_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    asyncio.run(dispatcher._dispatch_client_ai("client-1", {"message": "", "session_id": ""}))
    assert sent == []


def test_dispatch_client_ai_handles_engine_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: List[Dict[str, Any]] = []

    def _raise_engine() -> Any:
        raise RuntimeError("engine not ready")

    def fake_capture(exc: Exception, context: Dict[str, Any]) -> None:
        captured.append({"exc": str(exc), "context": context})

    monkeypatch.setattr(dispatcher.CognitiveEngine, "get_instance", _raise_engine)
    monkeypatch.setattr(dispatcher, "capture_exception", fake_capture)
    asyncio.run(dispatcher._dispatch_client_ai("client-1", {"message": "hola", "session_id": "s1"}))
    assert captured and captured[0]["context"]["source"] == "client_ai"


def test_handle_meta_event_calls_raw_and_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []
    ai_calls: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    async def fake_dispatch(payload: Dict[str, Any]) -> None:
        ai_calls.append(payload)

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    monkeypatch.setattr(dispatcher, "_dispatch_meta_ai", fake_dispatch)

    payload = {"entry": []}
    asyncio.run(dispatcher.handle_meta_event(payload))

    assert sent and sent[0]["source"] == "meta_raw"
    assert ai_calls == [payload]


def test_handle_client_event_runs_ai_for_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []
    ai_calls: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    async def fake_dispatch(client_id: str, payload: Dict[str, Any]) -> None:
        ai_calls.append({"client_id": client_id, "payload": payload})

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    monkeypatch.setattr(dispatcher, "_dispatch_client_ai", fake_dispatch)

    payload = {"mode": "chat", "message": "hola", "session_id": "s1"}
    asyncio.run(dispatcher.handle_client_event("client-1", payload))

    assert sent and sent[0]["source"] == "client_raw"
    assert ai_calls == [{"client_id": "client-1", "payload": payload}]


def test_handle_client_event_skips_ai_for_non_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []
    ai_calls: List[Dict[str, Any]] = []

    async def fake_send(payload: Dict[str, Any]) -> Dict[str, Any]:
        sent.append(payload)
        return {"dispatched": True, "pending": True, "confirmed": False, "execution_status": "accepted"}

    async def fake_dispatch(client_id: str, payload: Dict[str, Any]) -> None:
        ai_calls.append({"client_id": client_id, "payload": payload})

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", fake_send)
    monkeypatch.setattr(dispatcher, "_dispatch_client_ai", fake_dispatch)

    payload = {"mode": "webhook", "data": {"x": 1}}
    asyncio.run(dispatcher.handle_client_event("client-1", payload))
    assert sent and sent[0]["source"] == "client_raw"
    assert ai_calls == []


def test_handle_shopify_event_handles_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: List[Dict[str, Any]] = []

    async def raise_send(_payload: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("n8n down")

    def fake_capture(exc: Exception, context: Dict[str, Any]) -> None:
        captured.append({"exc": str(exc), "context": context})

    monkeypatch.setattr(dispatcher, "send_to_n8n_with_response", raise_send)
    monkeypatch.setattr(dispatcher, "capture_exception", fake_capture)

    asyncio.run(dispatcher.handle_shopify_event({"id": 1}, "orders/create", "acme.myshopify.com"))

    assert captured and captured[0]["context"]["source"] == "shopify"


def test_should_run_agentbridge_direct_filters_smalltalk() -> None:
    assert dispatcher._should_run_agentbridge_direct("hola") is False
    assert dispatcher._should_run_agentbridge_direct("gracias") is False
    assert dispatcher._should_run_agentbridge_direct("abre crm y busca prospectos") is True


def test_format_operator_reply_is_structured() -> None:
    reply = dispatcher._format_operator_reply(
        goal="Busca clientes para clínicas",
        status="completed",
        summary="Generé 25 prospectos con contacto.",
        error="",
        steps=[{"status": "completed"}, {"status": "ok"}, {"status": "failed"}],
    )
    assert reply == "✅ Generé 25 prospectos con contacto."
