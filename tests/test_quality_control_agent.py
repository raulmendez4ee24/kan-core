from __future__ import annotations

import asyncio

from brain.agents.quality_control_agent import (
    ImagePipeline,
    ImageResult,
    QualityControlAgent,
    QualityIssue,
    QualityReport,
    Severity,
    Verdict,
)


def _issue(severity: Severity) -> QualityIssue:
    return QualityIssue(
        category="brand",
        severity=severity,
        description=f"{severity.value} issue",
        fix_instruction="fix it",
        confidence=0.9,
    )


def test_brand_director_mode_uses_relaxed_minor_fixes_threshold() -> None:
    agent = QualityControlAgent()
    verdict = agent._decide_verdict(
        61,
        [_issue(Severity.MAJOR)],
        1,
        content_type="offer_launch",
        brand_director_mode=True,
    )
    assert verdict == Verdict.MINOR_FIXES


def test_brand_director_mode_rejects_only_below_50() -> None:
    agent = QualityControlAgent()
    verdict = agent._decide_verdict(
        49,
        [],
        1,
        content_type="offer_launch",
        brand_director_mode=True,
    )
    assert verdict == Verdict.REJECTED


def test_brand_director_mode_rejects_with_two_critical_issues() -> None:
    agent = QualityControlAgent()
    verdict = agent._decide_verdict(
        72,
        [_issue(Severity.CRITICAL), _issue(Severity.CRITICAL)],
        1,
        content_type="offer_launch",
        brand_director_mode=True,
    )
    assert verdict == Verdict.REJECTED


def test_ads_context_keeps_strict_thresholds() -> None:
    agent = QualityControlAgent()
    verdict = agent._decide_verdict(
        65,
        [],
        1,
        content_type="ad_creative",
        brand_director_mode=False,
    )
    assert verdict == Verdict.REJECTED


def test_brand_director_scores_ai_artifacts_with_relaxed_floor() -> None:
    agent = QualityControlAgent()
    analysis = {
        "_content_type": "offer_launch",
        "_brand_director_mode": True,
        "text_analysis": {"typography_quality": 40},
        "color_analysis": {"color_harmony": 68, "background_is_dark": True},
        "composition_analysis": {"composition_score": 62},
        "ai_artifact_analysis": {"overall_artifact_score": 32, "critical_artifacts": []},
        "brand_analysis": {"brand_consistency_score": 41},
        "emotional_analysis": {"emotional_impact_score": 55},
    }
    scores = asyncio.run(agent._score_categories(analysis))
    assert scores["ai_artifact_free"] == 70
    assert scores["text_quality"] == 70
    assert scores["brand_consistency"] == 70


def test_image_pipeline_auto_enables_brand_director_mode_for_brand_content() -> None:
    captured: dict[str, object] = {}

    class DummyAssetManager:
        async def generate_post_image_bytes(self, **kwargs):
            return ImageResult(image=b"img", prompt_used="prompt", asset_id="asset-1")

        async def upload_image_bytes(self, image: bytes, asset_id: str) -> str:
            return f"https://example.com/{asset_id}.jpg"

    class DummyQC:
        max_attempts = 3

        async def review(self, **kwargs):
            captured.update(kwargs)
            return QualityReport(verdict=Verdict.APPROVED, overall_score=70)

    pipeline = ImagePipeline(DummyAssetManager(), qc_agent=DummyQC())
    result = asyncio.run(
        pipeline.generate_and_publish(
            topic="Tema",
            hook_text="Hook",
            style_preset="premium",
            vertical="clinics",
            content_type="offer_launch",
        )
    )

    assert result["status"] == "published"
    assert captured["brand_director_mode"] is True
