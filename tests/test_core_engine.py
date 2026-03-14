import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import brain.core as core_module
from brain.core import CognitiveEngine
from brain.memory import NoOpMemory


class _Scalars:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def all(self) -> List[Any]:
        return list(self._rows)


class _ExecuteResult:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class FakeSession:
    def __init__(self, execute_rows: List[List[Any]], client: Any = None) -> None:
        self._execute_rows = list(execute_rows)
        self._client = client
        self.added: List[Any] = []
        self.added_many: List[Any] = []
        self.commits = 0

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._client

    async def execute(self, _stmt: Any) -> _ExecuteResult:
        if not self._execute_rows:
            return _ExecuteResult([])
        return _ExecuteResult(self._execute_rows.pop(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: List[Any]) -> None:
        self.added_many.extend(objs)

    async def commit(self) -> None:
        self.commits += 1


class MemoryOk:
    def __init__(self) -> None:
        self.search_calls: List[Dict[str, Any]] = []
        self.store_calls: List[Dict[str, Any]] = []

    async def search_context(self, query: str, *, top_k: int = 5, filters: Dict[str, Any] | None = None) -> List[str]:
        self.search_calls.append({"query": query, "filters": filters, "top_k": top_k})
        return ["customer asked about order status"]

    async def store_interaction(self, **kwargs: Any) -> None:
        self.store_calls.append(kwargs)


class MemoryFail:
    async def search_context(self, query: str, *, top_k: int = 5, filters: Dict[str, Any] | None = None) -> List[str]:
        raise RuntimeError("memory down")

    async def store_interaction(self, **kwargs: Any) -> None:
        raise RuntimeError("memory down")


class _AsyncSessionCtx:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> Dict[str, Any]:
        return self._payload


@pytest.fixture
def engine() -> CognitiveEngine:
    eng = object.__new__(CognitiveEngine)
    eng._api_key = "k"
    eng._provider = "gemini"
    eng._base_url = "https://example.test"
    eng._timeout = 10.0
    eng._retries = 1
    eng._backoff_base = 0.1
    eng._backoff_max = 1.0
    eng._max_output_tokens = None
    eng._max_context_chars = 200
    eng._enable_bias_guard = True
    eng._memory = MemoryOk()
    return eng


def test_parse_client_id(engine: CognitiveEngine) -> None:
    cid = str(uuid.uuid4())
    assert str(engine._parse_client_id(cid)) == cid

    with pytest.raises(ValueError):
        engine._parse_client_id("not-a-uuid")


def test_init_and_get_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CognitiveEngine, "_build_memory", lambda self: NoOpMemory())
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    CognitiveEngine._instance = None

    first = CognitiveEngine.get_instance()
    second = CognitiveEngine.get_instance()
    assert first is second
    assert first._provider == "openai"

    CognitiveEngine._instance = None
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    third = CognitiveEngine.get_instance()
    assert third._provider == "openai"
    assert third._api_key == ""


def test_detect_provider_respects_explicit_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    assert CognitiveEngine._detect_provider() == "gemini"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert CognitiveEngine._detect_provider() == "openai"


def test_extract_action_strict_contract(engine: CognitiveEngine) -> None:
    valid = (
        '{"contract_version":"1.0","action":"create_order","integration":"shopify",'
        '"arguments":{"sku":"A1"},"rationale":"User requested checkout"}'
    )
    parsed = engine._extract_action(valid)
    assert parsed is not None
    assert parsed["contract_version"] == "1.0"
    assert parsed["action"] == "create_order"

    invalid_missing_version = (
        '{"action":"create_order","integration":"shopify",'
        '"arguments":{},"rationale":"x"}'
    )
    assert engine._extract_action(invalid_missing_version) is None


def test_extract_action_handles_codeblock_and_action_prefix(engine: CognitiveEngine) -> None:
    wrapped = (
        "```json\n"
        '{"contract_version":"1.0","action":"lookup_catalog","integration":"shopify",'
        '"arguments":{"sku":"A1"},"rationale":"Need data"}'
        "\n```"
    )
    parsed = engine._extract_action(wrapped)
    assert parsed is not None
    assert parsed["action"] == "lookup_catalog"

    prefixed = (
        "ACTION: "
        '{"contract_version":"1.0","action":"lookup_catalog","integration":"shopify",'
        '"arguments":{},"rationale":"Need data"}'
    )
    parsed2 = engine._extract_action(prefixed)
    assert parsed2 is not None


def test_extract_text_and_usage(monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine) -> None:
    payload = {
        "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 6,
            "totalTokenCount": 16,
        },
    }
    assert engine._extract_text(payload) == "hello"

    monkeypatch.setenv("GEMINI_COST_PER_INPUT_TOKEN", "0.001")
    monkeypatch.setenv("GEMINI_COST_PER_OUTPUT_TOKEN", "0.002")
    usage = engine._extract_usage(payload)
    assert usage is not None
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 6
    assert usage["cost_usd"] == 0.022

    assert engine._extract_usage({}) is None
    assert engine._extract_text({}) == ""


def test_build_request_includes_history_and_generation_config(engine: CognitiveEngine) -> None:
    engine._max_output_tokens = "128"
    history = [
        SimpleNamespace(role="user", content="hello"),
        SimpleNamespace(role="assistant", content="hi"),
    ]

    body = engine._build_request(
        system_prompt="sys",
        history=history,
        message="next",
        model_name="gemini-1.5-flash",
        temperature=0.3,
    )

    assert body["generationConfig"]["temperature"] == 0.3
    assert body["generationConfig"]["maxOutputTokens"] == 128
    assert body["contents"][0]["role"] == "user"
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][2]["parts"][0]["text"] == "next"


def test_load_history_reverses_order(engine: CognitiveEngine) -> None:
    logs_desc = [
        SimpleNamespace(role="assistant", content="new"),
        SimpleNamespace(role="user", content="old"),
    ]
    session = FakeSession(execute_rows=[logs_desc])

    rows = asyncio.run(engine._load_history(session, uuid.uuid4(), "s1"))
    assert [r.content for r in rows] == ["old", "new"]


def test_load_active_integrations(engine: CognitiveEngine) -> None:
    rows = [SimpleNamespace(name="crm"), SimpleNamespace(name="shopify")]
    session = FakeSession(execute_rows=[rows])

    integrations = asyncio.run(engine._load_active_integrations(session, uuid.uuid4()))
    assert [i.name for i in integrations] == ["crm", "shopify"]


def test_persist_helpers_commit(engine: CognitiveEngine) -> None:
    session = FakeSession(execute_rows=[])
    cid = uuid.uuid4()

    asyncio.run(engine._persist_conversation(session, cid, "s1", "hi", "hello"))
    asyncio.run(
        engine._persist_usage(
            session,
            cid,
            {"input_tokens": 1, "output_tokens": 2, "cost_usd": 0.1},
        )
    )

    assert len(session.added_many) == 2
    assert len(session.added) == 1
    assert session.commits == 2


def test_generate_with_session_action_flow(engine: CognitiveEngine) -> None:
    memory = MemoryOk()
    engine._memory = memory

    client = SimpleNamespace(
        system_prompt=None,
        model_name="gemini-1.5-flash",
        temperature=0.4,
    )
    history_rows = [SimpleNamespace(role="user", content="hola")]
    integration_rows = [
        SimpleNamespace(
            name="shopify",
            base_url="https://api.shopify.com",
            auth_type="bearer",
            integration_metadata={"scope": "orders"},
        )
    ]
    session = FakeSession(execute_rows=[integration_rows, history_rows], client=client)

    async def fake_call(model_name: str, _body: Dict[str, Any]) -> Dict[str, Any]:
        assert model_name == "gemini-1.5-flash"
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"contract_version":"1.0","action":"lookup_catalog",'
                                    '"integration":"shopify","arguments":{"sku":"A1"},'
                                    '"rationale":"Need product info"}'
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
                "totalTokenCount": 14,
            },
        }

    engine._call_model = fake_call  # type: ignore[method-assign]

    result = asyncio.run(
        engine._generate_with_session(
            session,
            client_id=str(uuid.uuid4()),
            session_id="sess-1",
            message="que precio tiene",
        )
    )

    assert result["type"] == "action"
    assert result["payload"]["contract_version"] == "1.0"
    assert len(memory.store_calls) == 2
    assert session.commits == 2


def test_generate_with_session_injects_sales_marketing_skill(engine: CognitiveEngine) -> None:
    memory = MemoryOk()
    engine._memory = memory

    client = SimpleNamespace(
        system_prompt=None,
        model_name="gemini-1.5-flash",
        temperature=0.4,
    )
    session = FakeSession(execute_rows=[[], []], client=client)

    async def fake_call(model_name: str, body: Dict[str, Any]) -> Dict[str, Any]:
        assert model_name == "gemini-1.5-flash"
        system_text = body["systemInstruction"]["parts"][0]["text"]
        assert "Relevant operating skills:" in system_text
        assert "kan_sales_marketing" in system_text
        assert "KAN Logic vende chatbots" in system_text
        return {
            "candidates": [{"content": {"parts": [{"text": "Yo te puedo ayudar a vender mas."}]}}],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8, "totalTokenCount": 28},
        }

    engine._call_model = fake_call  # type: ignore[method-assign]

    result = asyncio.run(
        engine._generate_with_session(
            session,
            client_id=str(uuid.uuid4()),
            session_id="sess-sales",
            message="quiero vender mas con meta ads y llevarlos de instagram a whatsapp",
        )
    )

    assert result["type"] == "message"
    assert result["content"].startswith("Yo te")


def test_generate_with_session_survives_memory_failures(engine: CognitiveEngine) -> None:
    engine._memory = MemoryFail()

    client = SimpleNamespace(
        system_prompt="custom",
        model_name="gemini-1.5-flash",
        temperature=0.4,
    )
    session = FakeSession(execute_rows=[[], []], client=client)

    async def fake_call(_model_name: str, _body: Dict[str, Any]) -> Dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": "respuesta"}]}}]}

    engine._call_model = fake_call  # type: ignore[method-assign]

    result = asyncio.run(
        engine._generate_with_session(
            session,
            client_id=str(uuid.uuid4()),
            session_id="sess-2",
            message="hola",
        )
    )

    assert result == {"type": "message", "content": "respuesta"}


def test_generate_response_uses_async_session_local(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = object.__new__(CognitiveEngine)
    session = object()

    async def fake_generate_with_session(_session: Any, **kwargs: Any) -> Dict[str, Any]:
        assert _session is session
        assert kwargs["client_id"] == "c1"
        return {"type": "message", "content": "ok"}

    engine._generate_with_session = fake_generate_with_session  # type: ignore[method-assign]
    monkeypatch.setattr(core_module, "AsyncSessionLocal", lambda: _AsyncSessionCtx(session))

    result = asyncio.run(engine.generate_response("c1", "s1", "hola"))
    assert result["content"] == "ok"


def test_call_gemini_uses_retry_and_parses_json(
    monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine
) -> None:
    captured: Dict[str, Any] = {}
    fake_response = _FakeResponse({"ok": True})

    async def fake_request_with_retry(
        _client: Any,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> _FakeResponse:
        captured.update({"method": method, "url": url, **kwargs})
        return fake_response

    monkeypatch.setattr(core_module, "request_with_retry", fake_request_with_retry)
    engine._base_url = "https://gemini.example"
    engine._api_key = "abc"

    result = asyncio.run(engine._call_gemini("gemini-test", {"x": 1}))
    assert result == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://gemini.example/models/gemini-test:generateContent"
    assert captured["params"] == {"key": "abc"}
    assert fake_response.raise_called is True


def test_build_request_openai_shape(engine: CognitiveEngine) -> None:
    engine._provider = "openai"
    engine._max_output_tokens = "256"
    history = [
        SimpleNamespace(role="user", content="hello"),
        SimpleNamespace(role="assistant", content="hi"),
    ]

    body = engine._build_request(
        system_prompt="sys",
        history=history,
        message="next",
        model_name="gpt-5.2-chat-latest",
        temperature=0.2,
    )

    assert body["model"] == "gpt-5.2-chat-latest"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][3]["content"] == "next"
    assert body["max_tokens"] == 256


def test_extract_text_and_usage_openai(
    monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine
) -> None:
    engine._provider = "openai"
    payload = {
        "choices": [{"message": {"content": "hola desde gpt"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
    }
    monkeypatch.setenv("OPENAI_COST_PER_INPUT_TOKEN", "0.001")
    monkeypatch.setenv("OPENAI_COST_PER_OUTPUT_TOKEN", "0.002")

    assert engine._extract_text(payload) == "hola desde gpt"
    usage = engine._extract_usage(payload)
    assert usage is not None
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 5
    assert usage["cost_usd"] == 0.017


def test_call_openai_uses_retry_and_parses_json(
    monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine
) -> None:
    captured: Dict[str, Any] = {}
    fake_response = _FakeResponse({"id": "chatcmpl_1"})

    async def fake_request_with_retry(
        _client: Any,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> _FakeResponse:
        captured.update({"method": method, "url": url, **kwargs})
        return fake_response

    monkeypatch.setattr(core_module, "request_with_retry", fake_request_with_retry)
    engine._provider = "openai"
    engine._base_url = "https://api.openai.test/v1"
    engine._api_key = "openai-secret"

    result = asyncio.run(
        engine._call_openai({"model": "gpt-5.2-chat-latest", "messages": []})
    )
    assert result == {"id": "chatcmpl_1"}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.openai.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer openai-secret"
    assert fake_response.raise_called is True


def test_resolve_model_name_falls_back_from_gemini_to_openai(
    monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine
) -> None:
    engine._provider = "openai"
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2-chat-latest")
    assert engine._resolve_model_name("gemini-1.5-flash") == "gpt-5.2-chat-latest"


def test_call_model_failover_openai_to_anthropic(monkeypatch: pytest.MonkeyPatch, engine: CognitiveEngine) -> None:
    engine._provider = "openai"
    monkeypatch.setenv("ENABLE_MODEL_FAILOVER", "true")
    monkeypatch.setenv("LLM_FAILOVER_CHAIN", "openai,anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-k")

    async def fail_openai(_body: Dict[str, Any], **_kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("openai down")

    async def ok_anthropic(_body: Dict[str, Any], **_kwargs: Any) -> Dict[str, Any]:
        return {"content": [{"type": "text", "text": "hola desde claude"}], "usage": {"input_tokens": 1, "output_tokens": 1}}

    monkeypatch.setattr(engine, "_call_openai", fail_openai)
    monkeypatch.setattr(engine, "_call_anthropic", ok_anthropic)

    body = {
        "model": "gpt-5.2-chat-latest",
        "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hola"}],
        "temperature": 0.2,
    }
    payload = asyncio.run(engine._call_model("gpt-5.2-chat-latest", body))
    assert payload["_provider"] == "anthropic"
    assert payload["_failover_trace"][0]["provider"] == "openai"
    assert "claude" in engine._extract_text(payload)


def test_extract_text_respects_payload_provider_override(engine: CognitiveEngine) -> None:
    engine._provider = "openai"
    payload = {
        "_provider": "anthropic",
        "content": [{"type": "text", "text": "anthropic content"}],
    }
    assert engine._extract_text(payload) == "anthropic content"
