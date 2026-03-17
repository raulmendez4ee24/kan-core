# brain/agents/quality_control_agent.py
# ═══════════════════════════════════════════════════════════════════
# QUALITY CONTROL AGENT — Rechaza todo lo que no sea perfecto
# ═══════════════════════════════════════════════════════════════════
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger("kan_core.agents.qc")


# ═══════════════════════════════════════════════════════
# ENUMS Y MODELOS
# ═══════════════════════════════════════════════════════

class Verdict(Enum):
    APPROVED = "approved"
    MINOR_FIXES = "minor_fixes"
    REJECTED = "rejected"
    ESCALATE = "escalate"


class Severity(Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


@dataclass
class QualityIssue:
    category: str
    severity: Severity
    description: str
    fix_instruction: str
    confidence: float


@dataclass
class QualityReport:
    verdict: Verdict
    overall_score: float
    issues: list[QualityIssue] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    regeneration_prompt: Optional[str] = None
    notes: str = ""
    reviewed_at: datetime = field(default_factory=datetime.now)
    attempt_number: int = 1


# ═══════════════════════════════════════════════════════
# BRAND STANDARDS
# ═══════════════════════════════════════════════════════

BRAND_STANDARDS = {
    "color": {
        "allowed_backgrounds": [
            "#0a0a14", "#0c0c14", "#0f0f13", "#12121e", "#080B14",
            "#0f0a08", "#050505",
        ],
        "forbidden_backgrounds": [
            "white", "light gray", "beige", "cream", "pastel",
            "any color with lightness > 30%",
        ],
        "accent_colors": {
            "default": "#e94560",
            "dental": "#3B82F6",
            "restaurant": "#eab308",
            "realestate": "#D4AF37",
            "gym": "#f97316",
            "beauty": "#f472b6",
        },
        "max_accent_colors": 2,
        "text_color": "#f0f0f5",
        "rules": [
            "Fondo SIEMPRE oscuro. Nunca claro. Nunca blanco.",
            "Máximo 2 colores de acento. Uno preferible.",
            "El acento se usa en UN elemento focal, no esparcido.",
            "Sin gradientes rainbow o multicolor.",
            "Sin neón excesivo o glow que distraiga.",
        ],
    },
    "typography": {
        "allowed_styles": [
            "geometric sans-serif", "modern sans-serif",
            "monospace for data", "clean slab-serif for editorial",
        ],
        "forbidden_styles": [
            "script", "handwritten", "decorative", "comic",
            "gothic", "grunge", "3D chrome", "bubble",
            "any font that looks 'fun' or 'playful'",
        ],
        "rules": [
            "Texto debe ser 100% legible a tamaño de celular.",
            "Jerarquía clara: headline GRANDE → sub medio → detail pequeño.",
            "Nunca más de 3 niveles de jerarquía de texto.",
            "Números y estadísticas deben ser 3x más grandes que body text.",
            "Kerning y spacing deben verse profesionales, no apretados.",
            "Texto NUNCA debe estar distorsionado, warped, o difuso.",
        ],
    },
    "composition": {
        "rules": [
            "UN punto focal claro. El ojo debe saber dónde ir primero.",
            "Mínimo 30% de espacio negativo (breathing room).",
            "Máximo 3 elementos principales en la composición.",
            "Logo de KAN Logic: bottom-right, pequeño, sutil.",
            "Safe zones: ningún elemento crítico en los bordes extremos.",
            "Balance visual: no todo amontonado en un lado.",
        ],
        "forbidden": [
            "Más de 5 elementos compitiendo por atención.",
            "Texto sobre áreas complejas sin contraste suficiente.",
            "Elementos cortados de forma no intencional.",
            "Composición simétrica aburrida sin tensión visual.",
        ],
    },
    "ai_artifacts": {
        "critical_defects": [
            "Manos con número incorrecto de dedos.",
            "Texto ilegible, distorsionado, o con letras mezcladas.",
            "Caras deformes o en el 'uncanny valley'.",
            "Objetos que se fusionan de forma antinatural.",
            "Texturas que se repiten en patrón visible (tiling).",
            "Bordes donde dos elementos se mezclan mal.",
            "Ojos que miran en direcciones diferentes.",
            "Dientes o sonrisas antinaturales.",
        ],
        "major_defects": [
            "Reflejos que no corresponden a la fuente de luz.",
            "Sombras inconsistentes con la iluminación.",
            "Texturas demasiado suaves o 'plastificadas'.",
            "Fondos que tienen elementos semi-formados.",
            "Profundidad de campo inconsistente.",
        ],
        "minor_defects": [
            "Micro-artifacts en áreas de bajo contraste.",
            "Textura ligeramente artificial en superficies.",
            "Bokeh que se ve un poco artificial.",
        ],
    },
    "brand": {
        "rules": [
            "Debe sentirse como KAN Logic, no como cualquier otra marca.",
            "Dark-mode SIEMPRE. Sin excepciones.",
            "Tech-forward: debe sentirse innovador y moderno.",
            "Debe pasar el 'Agency Test': ¿una agencia top lo postearía?",
            "No debe verse como template genérico de Canva.",
            "No debe verse como 'hecho por IA' a primera vista.",
            "Consistencia con posts anteriores en el feed.",
        ],
    },
    "thresholds": {
        "overall_minimum": 75,
        "critical_category_minimum": 60,
        "brand_director_overall_minimum": 65,
        "brand_director_minor_fix_minimum": 60,
        "brand_director_reject_below": 50,
        "brand_director_ai_artifact_minimum": 70,
        "categories": {
            "text_quality": 80,
            "color_compliance": 70,
            "composition": 70,
            "ai_artifact_free": 85,
            "brand_consistency": 75,
            "emotional_impact": 60,
        },
    },
}

_BRAND_DIRECTOR_CONTENT_TYPES = {
    "educational_tip",
    "testimonial",
    "offer_launch",
    "workflow_upgrade",
    "objection_breaker",
    "case_study",
    "brand_authority",
}


def _is_brand_director_context(content_type: str | None) -> bool:
    return str(content_type or "").strip().lower() in _BRAND_DIRECTOR_CONTENT_TYPES


def _use_brand_director_mode(content_type: str | None, brand_director_mode: bool = False) -> bool:
    return bool(brand_director_mode or _is_brand_director_context(content_type))


# ═══════════════════════════════════════════════════════
# VISION PROVIDER — Claude vision via httpx
# ═══════════════════════════════════════════════════════

class VisionProvider:
    """
    Wrapper de Claude vision para análisis de imágenes.
    Usa httpx directo contra Anthropic API (patrón del codebase).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.anthropic.com/v1",
    ):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model or os.getenv("QC_VISION_MODEL", "claude-sonnet-4-6")
        self._base_url = base_url.rstrip("/")

    async def analyze_image(
        self,
        image: bytes,
        prompt: str,
        response_format: str = "json",
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """
        Envía imagen + prompt a Claude vision.
        Devuelve dict parseado si response_format="json", o {"text": ...} si no.
        """
        encoded = base64.standard_b64encode(image).decode()
        system = (
            "You are a brutally honest creative director and quality control specialist. "
            "Your job is to protect brand quality. Never approve mediocre work. "
            "Always respond with valid JSON matching the exact schema requested."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/messages",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        raw_text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                raw_text = block.get("text", "")
                break

        if response_format == "json":
            try:
                # Claude sometimes wraps JSON in ```json ... ```
                text = raw_text.strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                return json.loads(text.strip())
            except Exception:
                logger.warning("QC vision: failed to parse JSON response, returning raw")
                return {"_raw": raw_text}
        return {"text": raw_text}


# ═══════════════════════════════════════════════════════
# QUALITY CONTROL AGENT
# ═══════════════════════════════════════════════════════

class QualityControlAgent:
    """
    Revisa cada imagen generada contra los estándares de marca.
    Usa Claude vision para analizar.
    Rechaza sin piedad lo que no cumple.
    """

    def __init__(
        self,
        vision_provider: VisionProvider | None = None,
        brand_standards: dict | None = None,
        max_attempts: int = 3,
    ):
        self.vision = vision_provider or VisionProvider()
        self.standards = brand_standards or BRAND_STANDARDS
        self.max_attempts = max_attempts
        self.history: list[dict] = []

    async def review(
        self,
        image: bytes,
        original_prompt: str,
        style_preset: str,
        hook_text: str,
        vertical: str | None = None,
        content_type: str = "general",
        attempt: int = 1,
        brand_director_mode: bool = False,
    ) -> QualityReport:
        analysis = await self._analyze_with_vision(
            image=image,
            context={
                "prompt": original_prompt,
                "style": style_preset,
                "hook": hook_text,
                "vertical": vertical,
                "content_type": content_type,
                "brand_director_mode": brand_director_mode,
            },
        )
        analysis["_content_type"] = content_type
        analysis["_brand_director_mode"] = brand_director_mode
        scores = await self._score_categories(analysis)
        issues = await self._detect_issues(analysis, scores)
        overall_score = self._calculate_overall_score(
            scores,
            issues,
            content_type=content_type,
            brand_director_mode=brand_director_mode,
        )
        verdict = self._decide_verdict(
            overall_score,
            issues,
            attempt,
            content_type=content_type,
            brand_director_mode=brand_director_mode,
        )

        regeneration_prompt = None
        if verdict in [Verdict.REJECTED, Verdict.MINOR_FIXES]:
            regeneration_prompt = await self._generate_fix_prompt(
                original_prompt=original_prompt,
                issues=issues,
                analysis=analysis,
            )

        report = QualityReport(
            verdict=verdict,
            overall_score=overall_score,
            issues=issues,
            scores=scores,
            regeneration_prompt=regeneration_prompt,
            attempt_number=attempt,
            notes=self._generate_notes(verdict, issues, overall_score),
        )

        self.history.append({
            "report": report,
            "prompt": original_prompt,
            "style": style_preset,
            "vertical": vertical,
        })

        logger.info(
            "QC review attempt=%d verdict=%s score=%.1f issues=%d",
            attempt, verdict.value, overall_score, len(issues),
        )
        return report

    async def _analyze_with_vision(self, image: bytes, context: dict) -> dict:
        brand_director_context = _use_brand_director_mode(
            context.get("content_type"),
            bool(context.get("brand_director_mode")),
        )
        text_artifact_guidance = (
            "IMPORTANTE: para contenido automatizado del Brand Director, el sistema ya agrega un overlay oscuro "
            "y reemplaza el texto con Pillow. Si ves letras, palabras o tipografía residual en el fondo generado, "
            "trátalo como defecto menor de background, no como fallo crítico, salvo que rompa claramente la lectura final."
            if brand_director_context
            else "Si ves texto distorsionado, evalúalo con severidad normal."
        )
        vision_prompt = f"""Eres un director de arte senior en una agencia de diseño premium.
Revisa esta imagen de Instagram para la marca KAN Logic (agencia tech mexicana de IA).

CONTEXTO:
- Style preset: {context['style']}
- Hook text que debería aparecer: "{context['hook']}"
- Vertical/industria: {context.get('vertical', 'general')}
- Tipo de contenido: {context['content_type']}
- Prompt original usado: {str(context['prompt'])[:500]}

REGLA ESPECIAL DE TEXTO:
{text_artifact_guidance}

ANALIZA CADA ASPECTO y devuelve SOLO JSON válido con esta estructura exacta:

{{
  "text_analysis": {{
    "is_text_present": true,
    "is_text_legible": true,
    "text_distortions": "ninguna",
    "text_reads_as": "texto visible",
    "matches_intended_hook": true,
    "typography_quality": 80,
    "hierarchy_clear": true,
    "font_style_appropriate": true,
    "issues": []
  }},
  "color_analysis": {{
    "background_is_dark": true,
    "estimated_bg_color": "#0a0a14",
    "accent_colors_used": ["#e94560"],
    "number_of_accent_colors": 1,
    "matches_brand_palette": true,
    "color_harmony": 85,
    "contrast_sufficient": true,
    "issues": []
  }},
  "composition_analysis": {{
    "clear_focal_point": true,
    "negative_space_percentage": 40,
    "number_of_competing_elements": 2,
    "balance": "bueno",
    "logo_placement": "correcta",
    "safe_zones_respected": true,
    "composition_score": 80,
    "issues": []
  }},
  "ai_artifact_analysis": {{
    "hands_visible": false,
    "hands_correct": null,
    "faces_visible": false,
    "faces_natural": null,
    "text_artifacts": "ninguno",
    "object_fusion_errors": false,
    "texture_tiling": false,
    "lighting_consistent": true,
    "shadows_consistent": true,
    "uncanny_valley_risk": 5,
    "overall_artifact_score": 90,
    "critical_artifacts": [],
    "minor_artifacts": []
  }},
  "brand_analysis": {{
    "feels_like_kan_logic": true,
    "feels_premium": true,
    "feels_tech_forward": true,
    "could_agency_post_this": true,
    "looks_ai_generated": false,
    "looks_like_canva_template": false,
    "brand_consistency_score": 80,
    "issues": []
  }},
  "emotional_analysis": {{
    "stops_the_scroll": true,
    "evokes_intended_emotion": true,
    "intended_emotion": "confianza",
    "emotional_impact_score": 75,
    "would_you_double_tap": true,
    "issues": []
  }},
  "overall_impression": {{
    "first_reaction": "imagen limpia y profesional",
    "best_element": "tipografía clara",
    "worst_element": "ninguno crítico",
    "professional_enough": true,
    "recommendation": "aprobar"
  }}
}}

SÉ BRUTALMENTE HONESTO. Devuelve SOLO el JSON, sin markdown."""

        try:
            result = await self.vision.analyze_image(
                image=image,
                prompt=vision_prompt,
                response_format="json",
                max_tokens=2500,
            )
            if "_raw" in result:
                logger.warning("QC vision returned unparseable response")
                return {}
            return result
        except Exception:
            logger.exception("QC vision analysis failed; using empty analysis")
            return {}

    async def _score_categories(self, analysis: dict) -> dict:
        brand_director_context = _use_brand_director_mode(
            analysis.get("_content_type"),
            bool(analysis.get("_brand_director_mode")),
        )
        critical_artifacts = analysis.get("ai_artifact_analysis", {}).get("critical_artifacts", [])
        text_residual_artifacts = [
            str(item).lower() for item in critical_artifacts
            if any(
                token in str(item).lower()
                for token in ("texto residual", "prompt visible", "text residual", "prompt text", "tipografía residual")
            )
        ]
        scores = {
            "text_quality": int(analysis.get("text_analysis", {}).get("typography_quality", 70)),
            "color_compliance": int(analysis.get("color_analysis", {}).get("color_harmony", 70)),
            "composition": int(analysis.get("composition_analysis", {}).get("composition_score", 70)),
            "ai_artifact_free": int(analysis.get("ai_artifact_analysis", {}).get("overall_artifact_score", 70)),
            "brand_consistency": int(analysis.get("brand_analysis", {}).get("brand_consistency_score", 70)),
            "emotional_impact": int(analysis.get("emotional_analysis", {}).get("emotional_impact_score", 65)),
        }

        if brand_director_context:
            # Brand Director assets are automated social content, not ad-grade hero images.
            # Start these categories at a sane baseline and only deduct for severe defects.
            scores["text_quality"] = max(scores["text_quality"], 70)
            scores["ai_artifact_free"] = max(
                scores["ai_artifact_free"],
                int(self.standards["thresholds"].get("brand_director_ai_artifact_minimum", 70)),
            )
            scores["brand_consistency"] = max(scores["brand_consistency"], 70)

        if not analysis.get("color_analysis", {}).get("background_is_dark", True):
            scores["color_compliance"] = min(scores["color_compliance"], 20)
            scores["brand_consistency"] = min(scores["brand_consistency"], 30)

        text_distortions = analysis.get("text_analysis", {}).get("text_distortions", "ninguna")
        if text_distortions == "severas":
            floor = 58 if brand_director_context else 10
            scores["text_quality"] = min(scores["text_quality"], floor)
        elif text_distortions == "menores":
            floor = 70 if brand_director_context else 50
            scores["text_quality"] = min(scores["text_quality"], floor)

        if len(critical_artifacts) > 0 and not (brand_director_context and len(text_residual_artifacts) == len(critical_artifacts)):
            floor = 55 if brand_director_context else 20
            scores["ai_artifact_free"] = min(scores["ai_artifact_free"], floor)

        if analysis.get("brand_analysis", {}).get("looks_ai_generated", False) and not brand_director_context:
            scores["brand_consistency"] = min(scores["brand_consistency"], 40)

        if analysis.get("brand_analysis", {}).get("looks_like_canva_template", False) and not brand_director_context:
            scores["brand_consistency"] = min(scores["brand_consistency"], 30)

        return scores

    async def _detect_issues(self, analysis: dict, scores: dict) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        brand_director_context = _use_brand_director_mode(
            analysis.get("_content_type"),
            bool(analysis.get("_brand_director_mode")),
        )

        text = analysis.get("text_analysis", {})
        if text.get("text_distortions") == "severas":
            issues.append(QualityIssue(
                category="text",
                severity=Severity.MINOR if brand_director_context else Severity.CRITICAL,
                description=(
                    "Texto residual en el fondo generado"
                    if brand_director_context
                    else "Texto severamente distorsionado o ilegible"
                ),
                fix_instruction=(
                    "Mantener el fondo limpio y confiar en el overlay oscuro + texto en Pillow."
                    if brand_director_context
                    else "Regenerar SIN texto en la imagen. Agregar texto como overlay post-generación con Pillow."
                ),
                confidence=0.95,
            ))
        if not text.get("is_text_legible", True) and text.get("is_text_present", False):
            issues.append(QualityIssue(
                category="text",
                severity=Severity.MINOR if brand_director_context else Severity.CRITICAL,
                description=(
                    "Texto residual de fondo no es legible"
                    if brand_director_context
                    else "Texto presente pero no legible a tamaño de celular"
                ),
                fix_instruction=(
                    "No penalizar el fondo si el overlay final conserva lectura clara."
                    if brand_director_context
                    else "Aumentar tamaño del texto. Asegurar contraste mínimo 4.5:1."
                ),
                confidence=0.9,
            ))
        if not text.get("matches_intended_hook", True) and text.get("is_text_present", False):
            issues.append(QualityIssue(
                category="text",
                severity=Severity.MINOR if brand_director_context else Severity.MAJOR,
                description=(
                    f"Texto residual de fondo no coincide con hook. Se lee: '{text.get('text_reads_as', '???')}'"
                    if brand_director_context
                    else f"Texto visible no coincide con hook. Se lee: '{text.get('text_reads_as', '???')}'"
                ),
                fix_instruction=(
                    "El hook final lo corrige Pillow; solo ajustar el fondo si la mancha tipográfica distrae demasiado."
                    if brand_director_context
                    else "Usar overlay approach: generar imagen sin texto y agregar hook exacto con Pillow."
                ),
                confidence=0.85,
            ))
        if not text.get("hierarchy_clear", True):
            issues.append(QualityIssue(
                category="text", severity=Severity.MINOR,
                description="Jerarquía tipográfica no es clara",
                fix_instruction="Especificar tamaños relativos en prompt: headline 3x más grande que subtext.",
                confidence=0.7,
            ))

        color = analysis.get("color_analysis", {})
        if not color.get("background_is_dark", True):
            issues.append(QualityIssue(
                category="color", severity=Severity.CRITICAL,
                description=f"Fondo claro ({color.get('estimated_bg_color', '?')}). Debe ser oscuro.",
                fix_instruction="CRITICAL: Background must be very dark, near black (#0a0a14). NEVER light or white.",
                confidence=0.95,
            ))
        if color.get("number_of_accent_colors", 0) > 2:
            issues.append(QualityIssue(
                category="color", severity=Severity.MAJOR,
                description=f"Demasiados colores de acento ({color.get('number_of_accent_colors')}). Máximo 2.",
                fix_instruction="Use ONLY ONE accent color. Everything else is dark background and white text.",
                confidence=0.8,
            ))
        if not color.get("contrast_sufficient", True):
            issues.append(QualityIssue(
                category="color", severity=Severity.MAJOR,
                description="Contraste insuficiente entre texto y fondo",
                fix_instruction="Agregar gradient overlay oscuro detrás del texto.",
                confidence=0.85,
            ))

        comp = analysis.get("composition_analysis", {})
        if not comp.get("clear_focal_point", True):
            issues.append(QualityIssue(
                category="composition", severity=Severity.MAJOR,
                description="No hay punto focal claro",
                fix_instruction="Single dominant focal element. Everything else is secondary.",
                confidence=0.75,
            ))
        if comp.get("number_of_competing_elements", 0) > 4:
            issues.append(QualityIssue(
                category="composition", severity=Severity.MAJOR,
                description=f"Demasiados elementos ({comp.get('number_of_competing_elements')}). Máximo 3.",
                fix_instruction="Minimalist composition. Maximum 3 visual elements. 40%+ negative space.",
                confidence=0.8,
            ))
        if comp.get("negative_space_percentage", 50) < 25:
            issues.append(QualityIssue(
                category="composition", severity=Severity.MINOR,
                description=f"Poco espacio negativo ({comp.get('negative_space_percentage')}%). Se siente apretado.",
                fix_instruction="Generous negative space. At least 40% of canvas should be empty dark space.",
                confidence=0.7,
            ))

        ai = analysis.get("ai_artifact_analysis", {})
        if ai.get("hands_visible", False) and not ai.get("hands_correct", True):
            issues.append(QualityIssue(
                category="ai_artifact", severity=Severity.CRITICAL,
                description="Manos con anatomía incorrecta",
                fix_instruction="No visible hands in the image, or show from behind with fingers not visible.",
                confidence=0.95,
            ))
        if ai.get("faces_visible", False) and not ai.get("faces_natural", True):
            issues.append(QualityIssue(
                category="ai_artifact", severity=Severity.CRITICAL,
                description="Rostro en uncanny valley",
                fix_instruction="No faces visible. If person appears, show from behind or silhouette only.",
                confidence=0.9,
            ))
        for artifact in ai.get("critical_artifacts", []):
            artifact_text = str(artifact)
            lowered_artifact = artifact_text.lower()
            is_text_residual = any(
                token in lowered_artifact
                for token in ("texto residual", "prompt visible", "text residual", "prompt text", "tipografía residual")
            )
            issues.append(QualityIssue(
                category="ai_artifact",
                severity=Severity.MINOR if brand_director_context and is_text_residual else Severity.CRITICAL,
                description=(
                    f"Residuo de texto visible en el fondo: {artifact_text}"
                    if brand_director_context and is_text_residual
                    else f"Artefacto crítico: {artifact_text}"
                ),
                fix_instruction=(
                    "Reducir texto residual en el fondo; el overlay final ya protege la legibilidad."
                    if brand_director_context and is_text_residual
                    else f"CRITICAL: Avoid {artifact_text}. Photorealistic quality. No AI artifacts."
                ),
                confidence=0.85,
            ))
        if not ai.get("lighting_consistent", True):
            issues.append(QualityIssue(
                category="ai_artifact", severity=Severity.MAJOR,
                description="Iluminación inconsistente",
                fix_instruction="Single light source from upper-left. All shadows consistent.",
                confidence=0.75,
            ))

        brand = analysis.get("brand_analysis", {})
        if not brand.get("feels_premium", True):
            issues.append(QualityIssue(
                category="brand", severity=Severity.MINOR if brand_director_context else Severity.MAJOR,
                description="La imagen no se siente premium",
                fix_instruction="Simplificar. Más espacio negativo. Menos elementos. La sofisticación viene de la restraint.",
                confidence=0.7,
            ))
        if brand.get("looks_ai_generated", False):
            issues.append(QualityIssue(
                category="brand", severity=Severity.MINOR if brand_director_context else Severity.MAJOR,
                description="Se ve obviamente generada por IA",
                fix_instruction="Must look designed by a human designer in Figma. No surreal elements. No over-processing.",
                confidence=0.8,
            ))
        if not brand.get("could_agency_post_this", True):
            issues.append(QualityIssue(
                category="brand", severity=Severity.MINOR if brand_director_context else Severity.MAJOR,
                description="No pasa el Agency Test",
                fix_instruction="Verificar: tipografía perfecta, composición intencional, paleta cohesiva, acabado profesional.",
                confidence=0.75,
            ))

        emotional = analysis.get("emotional_analysis", {})
        if not emotional.get("stops_the_scroll", True):
            issues.append(QualityIssue(
                category="emotional", severity=Severity.MINOR,
                description="No detiene el scroll. Falta impacto visual.",
                fix_instruction="Necesita más contraste o un hook visual más fuerte.",
                confidence=0.65,
            ))

        return issues

    def _calculate_overall_score(
        self,
        scores: dict,
        issues: list[QualityIssue],
        *,
        content_type: str | None = None,
        brand_director_mode: bool = False,
    ) -> float:
        weights = {
            "text_quality": 0.25,
            "ai_artifact_free": 0.25,
            "brand_consistency": 0.20,
            "composition": 0.12,
            "color_compliance": 0.10,
            "emotional_impact": 0.08,
        }
        brand_director_context = _use_brand_director_mode(content_type, brand_director_mode)
        weighted_score = sum(
            scores.get(cat, 50) * weight
            for cat, weight in weights.items()
        )
        critical_count = sum(1 for i in issues if i.severity == Severity.CRITICAL)
        if critical_count > 0 and not brand_director_context:
            weighted_score *= max(0.3, 1 - (critical_count * 0.25))
        major_count = sum(1 for i in issues if i.severity == Severity.MAJOR)
        if major_count > 2:
            weighted_score *= 0.92 if brand_director_context else 0.85
        return round(min(100, max(0, weighted_score)), 1)

    def _decide_verdict(
        self,
        overall_score: float,
        issues: list[QualityIssue],
        attempt: int,
        *,
        content_type: str | None = None,
        brand_director_mode: bool = False,
    ) -> Verdict:
        thresholds = self.standards["thresholds"]
        brand_director_context = _use_brand_director_mode(content_type, brand_director_mode)
        overall_minimum = (
            thresholds.get("brand_director_overall_minimum", 65)
            if brand_director_context
            else thresholds["overall_minimum"]
        )
        minor_fix_minimum = (
            thresholds.get("brand_director_minor_fix_minimum", 60)
            if brand_director_context
            else overall_minimum
        )
        reject_below = (
            thresholds.get("brand_director_reject_below", 50)
            if brand_director_context
            else 50
        )
        critical_count = sum(1 for i in issues if i.severity == Severity.CRITICAL)
        major_count = sum(1 for i in issues if i.severity == Severity.MAJOR)

        if critical_count >= 2:
            if attempt >= self.max_attempts:
                return Verdict.ESCALATE
            return Verdict.REJECTED

        if overall_score < reject_below:
            if attempt >= self.max_attempts:
                return Verdict.ESCALATE
            return Verdict.REJECTED

        if overall_score > minor_fix_minimum and critical_count == 0:
            return Verdict.MINOR_FIXES

        if overall_score >= overall_minimum and major_count > 0 and critical_count == 0:
            if major_count > 2:
                if attempt >= self.max_attempts:
                    return Verdict.ESCALATE
                return Verdict.REJECTED
            return Verdict.MINOR_FIXES

        if overall_score >= 85 and major_count == 0:
            return Verdict.APPROVED

        if overall_score >= overall_minimum and critical_count == 0:
            return Verdict.APPROVED

        if critical_count == 1:
            if attempt >= self.max_attempts:
                return Verdict.ESCALATE
            return Verdict.MINOR_FIXES if brand_director_context and overall_score >= minor_fix_minimum else Verdict.REJECTED

        if attempt >= self.max_attempts:
            return Verdict.ESCALATE
        return Verdict.REJECTED

    async def _generate_fix_prompt(
        self,
        original_prompt: str,
        issues: list[QualityIssue],
        analysis: dict,
    ) -> str:
        fix_instructions = [
            f"FIX REQUIRED: {issue.fix_instruction}"
            for issue in sorted(issues, key=lambda i: ["critical", "major", "minor", "suggestion"].index(i.severity.value))
            if issue.severity in [Severity.CRITICAL, Severity.MAJOR]
        ]
        fixes_section = "\n".join(fix_instructions)
        best_element = analysis.get("overall_impression", {}).get("best_element", "")
        keep_section = f"KEEP THIS (it was good): {best_element}" if best_element else ""

        return f"""{original_prompt}

═══ CORRECTIONS FROM QUALITY REVIEW (MANDATORY) ═══

{fixes_section}

{keep_section}

CRITICAL REMINDERS:
- Background MUST be dark (#0a0a14 or similar). NEVER light.
- Text MUST be perfectly crisp and legible. If unsure, generate WITHOUT text.
- NO AI artifacts: no extra fingers, no warped text, no uncanny faces.
- Maximum 2 accent colors. Preferably 1.
- This is attempt #{len(self.history) + 1}. The previous version was rejected.
  This version MUST address all the issues listed above.
""".strip()

    def _generate_notes(
        self,
        verdict: Verdict,
        issues: list[QualityIssue],
        score: float,
    ) -> str:
        if verdict == Verdict.APPROVED:
            return f"✅ APROBADA (Score: {score}/100). Lista para publicar."
        if verdict == Verdict.MINOR_FIXES:
            fixes = [i.description for i in issues if i.severity == Severity.MAJOR]
            return f"⚠️ CASI LISTA (Score: {score}/100). Ajustes: {'; '.join(fixes)}"
        if verdict == Verdict.REJECTED:
            criticals = [i.description for i in issues if i.severity == Severity.CRITICAL]
            return f"❌ RECHAZADA (Score: {score}/100). Problemas críticos: {'; '.join(criticals)}."
        if verdict == Verdict.ESCALATE:
            return f"🚨 ESCALADA A RAÚL (Score: {score}/100). {self.max_attempts} intentos fallidos."
        return ""


# ═══════════════════════════════════════════════════════
# IMAGE RESULT — Objeto de resultado de generación
# ═══════════════════════════════════════════════════════

@dataclass
class ImageResult:
    image: bytes
    prompt_used: str
    asset_id: str


# ═══════════════════════════════════════════════════════
# IMAGE PIPELINE — Genera → Revisa → Publica
# ═══════════════════════════════════════════════════════

class ImagePipeline:
    """
    Pipeline completo: genera, revisa con QC, y publica.
    Maneja el loop de regeneración automática.
    """

    def __init__(self, asset_manager: Any, qc_agent: QualityControlAgent | None = None):
        self.asset_manager = asset_manager
        self.qc = qc_agent or QualityControlAgent()

    async def generate_and_publish(
        self,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        vertical: str | None = None,
        content_type: str = "general",
        platform: str = "instagram",
        brand_director_mode: bool = False,
    ) -> dict[str, Any]:
        """
        Loop completo:
        1. Genera imagen (bytes, sin subir)
        2. QC la revisa
        3. Si rechazada → regenera con prompt mejorado
        4. Si aprobada → sube a Cloudinary y retorna URL
        5. Si 3 intentos fallan → escala a Raúl
        """
        current_prompt: str | None = None
        last_report: QualityReport | None = None
        last_image_result: ImageResult | None = None
        effective_brand_director_mode = _use_brand_director_mode(content_type, brand_director_mode)

        for attempt in range(1, self.qc.max_attempts + 1):
            # ── GENERAR ──
            image_result = await self.asset_manager.generate_post_image_bytes(
                topic=topic,
                hook_text=hook_text,
                style_preset=style_preset,
                vertical=vertical,
                content_type=content_type,
                custom_prompt=current_prompt,
            )
            last_image_result = image_result

            # ── REVISAR ──
            report = await self.qc.review(
                image=image_result.image,
                original_prompt=image_result.prompt_used,
                style_preset=style_preset,
                hook_text=hook_text,
                vertical=vertical,
                content_type=content_type,
                attempt=attempt,
                brand_director_mode=effective_brand_director_mode,
            )
            last_report = report

            if report.verdict == Verdict.APPROVED:
                url = await self.asset_manager.upload_image_bytes(image_result.image, image_result.asset_id)
                return {
                    "status": "published",
                    "attempt": attempt,
                    "score": report.overall_score,
                    "published_url": url,
                }

            elif report.verdict == Verdict.MINOR_FIXES:
                fixed = await self._apply_minor_fixes(image_result.image, report.issues)
                url = await self.asset_manager.upload_image_bytes(fixed, image_result.asset_id)
                return {
                    "status": "published_with_fixes",
                    "attempt": attempt,
                    "score": report.overall_score,
                    "fixes_applied": [i.description for i in report.issues if i.severity == Severity.MAJOR],
                    "published_url": url,
                }

            elif report.verdict == Verdict.REJECTED:
                current_prompt = report.regeneration_prompt
                logger.info("QC rejected attempt %d — retrying with improved prompt", attempt)

            elif report.verdict == Verdict.ESCALATE:
                await self._escalate_to_raul(
                    topic=topic,
                    hook_text=hook_text,
                    attempts=attempt,
                    last_report=report,
                )
                return {
                    "status": "escalated",
                    "attempt": attempt,
                    "score": report.overall_score,
                    "reason": report.notes,
                }

        # Todos los intentos fallaron
        if last_report and last_image_result:
            await self._escalate_to_raul(
                topic=topic,
                hook_text=hook_text,
                attempts=self.qc.max_attempts,
                last_report=last_report,
            )
        return {
            "status": "escalated",
            "attempt": self.qc.max_attempts,
            "score": last_report.overall_score if last_report else 0,
            "reason": "Maximum attempts reached",
        }

    async def _apply_minor_fixes(self, image: bytes, issues: list[QualityIssue]) -> bytes:
        """
        Aplica fixes menores con Pillow sin regenerar.
        Ajusta contraste/brillo si hay problemas de color.
        """
        try:
            from io import BytesIO
            from PIL import Image, ImageEnhance

            img = Image.open(BytesIO(image)).convert("RGB")

            # Aumentar contraste si hay issues de contraste
            has_contrast_issue = any("contraste" in i.description.lower() for i in issues)
            if has_contrast_issue:
                img = ImageEnhance.Contrast(img).enhance(1.2)

            # Aumentar nitidez si hay issues de texto
            has_text_issue = any(i.category == "text" and i.severity == Severity.MINOR for i in issues)
            if has_text_issue:
                img = ImageEnhance.Sharpness(img).enhance(1.3)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92, optimize=True)
            return buf.getvalue()
        except Exception:
            logger.exception("Minor fixes failed, returning original image")
            return image

    async def _escalate_to_raul(
        self,
        topic: str,
        hook_text: str,
        attempts: int,
        last_report: QualityReport,
    ) -> None:
        """Manda WhatsApp a Raúl cuando el QC falla 3 veces."""
        try:
            from tools.ycloud_client import send_ycloud_whatsapp_text_message

            raul_phone = os.getenv("RAUL_PHONE", os.getenv("OWNER_PHONE", ""))
            sender = os.getenv("YCLOUD_WHATSAPP_FROM", "")

            if not raul_phone or not sender:
                logger.warning("QC escalation: RAUL_PHONE or YCLOUD_WHATSAPP_FROM not set")
                return

            issues_text = "\n".join(
                f"• {i.description}" for i in last_report.issues
                if i.severity in [Severity.CRITICAL, Severity.MAJOR]
            ) or "Issues de calidad general"

            message = (
                f"🚨 *Control de Calidad — Escalación*\n\n"
                f"No pude generar una imagen que pase el QC después de {attempts} intentos.\n\n"
                f"*Tema:* {topic}\n"
                f"*Hook:* {hook_text}\n"
                f"*Score final:* {last_report.overall_score}/100\n\n"
                f"*Problemas encontrados:*\n{issues_text}\n\n"
                f"¿Publico la mejor versión o salto este post?"
            )

            await send_ycloud_whatsapp_text_message(
                from_number=sender,
                to_number=raul_phone,
                message_body=message,
            )
            logger.info("QC escalation sent to Raúl via WhatsApp")
        except Exception:
            logger.exception("QC escalation WhatsApp failed")


# ═══════════════════════════════════════════════════════
# SINGLETON FACTORY
# ═══════════════════════════════════════════════════════

_qc_agent: QualityControlAgent | None = None


def get_qc_agent() -> QualityControlAgent:
    global _qc_agent
    if _qc_agent is None:
        _qc_agent = QualityControlAgent(
            vision_provider=VisionProvider(),
            max_attempts=int(os.getenv("QC_MAX_ATTEMPTS", "3")),
        )
    return _qc_agent
