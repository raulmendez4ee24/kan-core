from tools.whisper_client import _is_noise_transcript


def test_noise_transcript_detection() -> None:
    assert _is_noise_transcript("...")
    assert _is_noise_transcript("(silence)")
    assert _is_noise_transcript("eh")
    assert not _is_noise_transcript("que estoy haciendo")
    assert not _is_noise_transcript("abre shopify y revisa pedidos")

