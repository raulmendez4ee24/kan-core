import recovery.error_detector as error_detector


def test_detect_error_returns_fallback_when_missing_after_image() -> None:
    result = error_detector.detect_error(
        before_image_bytes=b"before",
        after_image_bytes=b"",
        instruction="Click login button",
        expected_outcome="Dashboard opens",
    )

    assert result.success is False
    assert result.error_type == "unknown"
    assert "Missing after screenshot" in result.reason


def test_detect_error_parses_model_json(monkeypatch) -> None:
    class FakeResponse:
        output_text = (
            '{"success":false,"error_type":"element_not_found","reason":"Login button not visible",'
            '"suggested_fix":"search_again","confidence":0.91}'
        )

    class FakeResponses:
        @staticmethod
        def create(**_kwargs):
            return FakeResponse()

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(error_detector, "_get_openai_client", lambda: FakeClient())

    result = error_detector.detect_error(
        before_image_bytes=b"before",
        after_image_bytes=b"after",
        instruction="Click login button",
        expected_outcome="Dashboard opens",
    )

    assert result.success is False
    assert result.error_type == "element_not_found"
    assert result.suggested_fix == "search_again"
    assert result.confidence == 0.91
