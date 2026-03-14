from __future__ import annotations

from brain.creative_patterns import load_creative_patterns


def test_creative_patterns_loads_expected_catalog() -> None:
    patterns = load_creative_patterns()

    assert len(patterns) >= 20
    assert any(item.pattern_name == "curiosity_gap" for item in patterns)
    assert any(item.pattern_name == "pain_loss" for item in patterns)
    assert any(item.pattern_name == "before_after" for item in patterns)
    assert len({item.category for item in patterns}) >= 8
