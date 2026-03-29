from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from tempfile import gettempdir
from time import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

if TYPE_CHECKING:
    from brain.brand_director import ContentPost

logger = logging.getLogger("kan_core.asset_manager")

_BACKGROUND = "#0a0a14"
_ACCENT = "#e94560"
_TEXT = "#f0f0f5"
_MUTED = "#b8bfd6"
_DEFAULT_GEMINI_IMAGE_MODEL = "models/gemini-3-pro-image-preview"
_DEFAULT_FAL_MODEL = "fal-ai/flux-pro/v1.1"
_DEFAULT_OPENAI_IMAGE_MODEL = "dall-e-3"

# ─── Aspect ratio mappings per format ────────────────────────────────────────
FORMAT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "static": (1080, 1080),
    "story": (1080, 1920),
    "reel": (1080, 1920),
    "landscape": (1920, 1080),
    "carousel": (1080, 1350),
    "cover": (1584, 396),
}

FORMAT_ASPECT_RATIOS: dict[str, str] = {
    "square": "1:1",
    "static": "1:1",
    "story": "9:16",
    "reel": "9:16",
    "landscape": "16:9",
    "carousel": "4:5",
    "cover": "4:1",
}

# fal.ai uses specific size names
_FAL_SIZE_MAP: dict[str, str] = {
    "square": "square_hd",
    "static": "square_hd",
    "story": "portrait_16_9",
    "reel": "portrait_16_9",
    "landscape": "landscape_16_9",
    "carousel": "portrait_4_3",
    "cover": "landscape_16_9",
}

# DALL-E 3 supported sizes
_DALLE_SIZE_MAP: dict[str, str] = {
    "square": "1024x1024",
    "static": "1024x1024",
    "story": "1024x1792",
    "reel": "1024x1792",
    "landscape": "1792x1024",
    "carousel": "1024x1792",
    "cover": "1792x1024",
}

_MAX_PROVIDER_RETRIES = 2
_RETRY_BACKOFF_SECONDS = [2, 5]
_SORA_BOLD_URLS = (
    "https://github.com/google/fonts/raw/main/ofl/sora/static/Sora-Bold.ttf",
    "https://github.com/google/fonts/raw/main/ofl/sora/Sora%5Bwght%5D.ttf",
)
_JETBRAINS_MONO_URLS = (
    "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Medium.ttf",
    "https://github.com/JetBrains/JetBrainsMono/raw/master/fonts/ttf/JetBrainsMono-Regular.ttf",
)
_SORA_FONT_NAME = "Sora-Bold.ttf"
_JETBRAINS_MONO_NAME = "JetBrains-Mono-Medium.ttf"
_FONT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_DOWNLOAD_ATTEMPTED: set[str] = set()
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)

BRAND: dict[str, Any] = {
    "name": "KAN Logic",
    "positioning": "automation and growth systems for Mexican businesses",
    "palette": {
        "background": "#0a0a14",
        "accent": "#e94560",
        "text": "#f5f7ff",
        "muted": "#b8bfd6",
    },
    "voice": [
        "direct",
        "premium",
        "commercial",
        "tech-forward",
        "clean",
        "Mexican business context",
    ],
    "visual_rules": [
        "no watermark",
        "no UI chrome",
        "no fake screenshots",
        "clean composition",
        "leave clean space for text overlay",
        "no text in the image",
    ],
}

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "premium": {
        "mood": "premium, modern, commercial confidence",
        "scene": "clean tech-forward business environment with aspirational polish",
        "composition": "hero-led framing with strong negative space for overlay",
        "palette_hint": "KAN Logic navy and coral with elevated neutral highlights",
        "objects": ["laptop", "smartphone", "subtle interface glow"],
    },
    "default": {
        "mood": "modern operational excellence",
        "scene": "clean premium business setting with subtle depth",
        "composition": "strong focal subject with clean negative space on the left",
        "palette_hint": "deep navy base with hot coral accent and soft neutral highlights",
        "objects": ["laptop", "smartphone", "abstract automation flows"],
    },
    "clinics": {
        "mood": "trustworthy clinical efficiency",
        "scene": "modern medical consultation environment",
        "composition": "clean sterile framing with negative space for overlay",
        "palette_hint": "blue-white medical palette with restrained KAN coral accents",
        "objects": ["reception desk", "consult room", "tablet"],
    },
    "dental": {
        "mood": "clean precision and confidence",
        "scene": "premium dental clinic environment",
        "composition": "bright professional frame with calm negative space",
        "palette_hint": "fresh green-white dental palette with subtle coral accents",
        "objects": ["dental chair", "reception", "smiling client silhouette"],
    },
    "restaurants": {
        "mood": "busy profitable hospitality",
        "scene": "stylized restaurant service environment",
        "composition": "dynamic service moment with negative space for hook",
        "palette_hint": "warm orange-gold hospitality palette balanced with KAN coral accents",
        "objects": ["table service", "phone orders", "kitchen pass"],
    },
    "spas": {
        "mood": "calm premium wellness",
        "scene": "elegant spa and self-care setting",
        "composition": "soft luxurious frame with calm negative space",
        "palette_hint": "soft purple and cream wellness palette with subtle coral accents",
        "objects": ["towels", "ambient light", "treatment room"],
    },
    "barbershops": {
        "mood": "sharp local authority",
        "scene": "premium barbershop with urban polish",
        "composition": "confident subject framing with clean space for overlay",
        "palette_hint": "charcoal, steel, and warm amber balanced with KAN coral accent",
        "objects": ["barber chair", "mirror lights", "appointment notebook"],
    },
    "real_estate": {
        "mood": "high-value advisory",
        "scene": "premium real estate presentation setting",
        "composition": "architectural framing with sales-focused negative space",
        "palette_hint": "navy, sand, and gold with restrained coral accent",
        "objects": ["modern home", "tablet presentation", "city view"],
    },
    "editorial": {
        "mood": "editorial, premium, strategic authority",
        "scene": "luxury business editorial setting with strong shadows",
        "composition": "magazine-grade asymmetry with controlled negative space",
        "palette_hint": "dark editorial neutrals with restrained coral accent",
        "objects": ["desk details", "printed materials", "architectural textures"],
    },
    "human": {
        "mood": "credible, candid, human-centered trust",
        "scene": "real operator moment inside a business workflow",
        "composition": "documentary framing with authentic negative space",
        "palette_hint": "organic dark neutrals with subtle warm highlights and coral accent",
        "objects": ["phone", "notebook", "real workspace details"],
    },
    "documentary": {
        "mood": "raw, credible, observational",
        "scene": "candid business moment with grounded realism",
        "composition": "reportage framing, natural imperfection, disciplined reading lane",
        "palette_hint": "film-like dark neutrals with restrained coral accent",
        "objects": ["workspace", "device glow", "authentic business textures"],
    },
    "glassmorphism_dark": {
        "mood": "tech-noir infrastructure, premium, architectural realism",
        "scene": (
            "Modern server room behind glass partition at night. "
            "Racks of servers with subtle blue and orange LED indicators visible through smoked glass. "
            "Real datacenter photography."
        ),
        "composition": "architectural perspective with layered glass reflections and a clean dark reading lane on the left",
        "palette_hint": "deep black, smoked glass, restrained cyan and amber practical light, no monochrome teal wash",
        "objects": ["server racks", "smoked glass partition", "subtle LED indicators"],
    },
    "terminal_luxury": {
        "mood": "command-line power, premium industrial design, real product photography",
        "scene": (
            "Close-up of a premium dark desk surface with a single high-end monitor showing terminal "
            "with green text on black. The monitor glow illuminates the brushed aluminum desk surface. "
            "One amber desk lamp. Real product photography, not render."
        ),
        "composition": "tight product-led crop with negative space on the left and one dominant light source",
        "palette_hint": "brushed aluminum, charcoal, black glass, terminal green glow, one amber practical accent",
        "objects": ["high-end monitor", "brushed aluminum desk", "amber desk lamp"],
    },
    "hardware_precision": {
        "mood": "precision engineering fetish, macro realism, luxury product campaign",
        "scene": (
            "Macro photograph of the inside of a premium laptop, motherboard visible. "
            "Copper traces, black capacitors, precision soldering. Shot like a Rolex ad — every component is beautiful. "
            "Real macro photography, ring light, shallow depth of field."
        ),
        "composition": "macro hero crop with disciplined focus plane and dark negative space where possible",
        "palette_hint": "copper, graphite, matte black, subtle highlight rolloff, zero fake neon",
        "objects": ["motherboard", "copper traces", "precision soldering", "black capacitors"],
    },
    "swiss_futurism": {
        "mood": "architectural rigor, mathematical calm, Japanese-Swiss precision",
        "scene": (
            "Architectural photograph of a Tadao Ando concrete building interior. "
            "Pure geometric lines, raw concrete, indirect natural light creating precise shadows. "
            "Minimal, mathematical, Japanese-Swiss precision. Real architecture, not render."
        ),
        "composition": "strict geometric framing with clean left-side negative space and shadow-led rhythm",
        "palette_hint": "raw concrete, deep shadow, cool daylight, restrained coral only as tiny accent if needed",
        "objects": ["concrete walls", "architectural voids", "precise shadow lines"],
    },
    "neural_dark": {
        "mood": "urban systems intelligence, dark cinematic realism, long-exposure energy",
        "scene": (
            "Long exposure photograph of a city at night from above. "
            "Light trails from cars create neural-network-like patterns. "
            "Deep black sky, orange and cyan light trails. Real photography, long exposure, tripod shot."
        ),
        "composition": "elevated urban viewpoint with layered light trails and a dark clean lane for text on the left",
        "palette_hint": "deep black sky, restrained cyan and orange light trails, no synthetic holograms",
        "objects": ["city grid", "light trails", "night skyline"],
    },
    "tactile_interface": {
        "mood": "premium hardware fetish, tactile detail, macro product realism",
        "scene": (
            "Close-up product photograph of a Teenage Engineering OP-1 synthesizer or similar premium hardware device. "
            "Matte black surface, orange knobs, precise typography on buttons. "
            "Shot on macro lens with shallow depth of field. Real product, real photo."
        ),
        "composition": "macro tactile crop with one hero control area and negative space preserved on the left",
        "palette_hint": "matte black, warm orange controls, subtle off-white legends, no CGI sheen",
        "objects": ["premium hardware device", "orange knobs", "tactile buttons"],
    },
    "gym": {
        "mood": "raw power, disciplined energy, premium athletic authority",
        "scene": (
            "Premium gym interior shot during golden hour. Heavy equipment with matte black finishes, "
            "rubber flooring, and controlled dramatic lighting from skylights. "
            "Empty station ready for peak performance. Real photography."
        ),
        "composition": "dynamic diagonal framing with strong depth and clean left space for overlay",
        "palette_hint": "matte black, deep charcoal, restrained red-orange accent on one element only",
        "objects": ["weight rack", "gym floor", "premium equipment", "dramatic shadow"],
    },
    "beauty": {
        "mood": "luxurious serenity, feminine power, editorial beauty",
        "scene": (
            "Close-up beauty editorial setting with soft diffused lighting. "
            "Premium skincare or beauty products arranged on marble surface. "
            "Soft reflections, controlled bokeh, magazine-quality product photography."
        ),
        "composition": "intimate macro-style crop with elegant negative space and soft gradient backdrop",
        "palette_hint": "soft rose, warm nude tones, dark base with one delicate pink accent",
        "objects": ["beauty product", "marble surface", "soft fabric", "ambient glow"],
    },
    "ecommerce": {
        "mood": "conversion-ready, trust-building, commercial clarity",
        "scene": (
            "Clean product photography on dark surface. Single hero product with controlled studio lighting. "
            "Professional e-commerce aesthetic with premium dark background. "
            "Subtle gradient and reflection on surface. Real product photo."
        ),
        "composition": "centered product hero with generous clean margins and subtle surface reflection",
        "palette_hint": "dark navy base, controlled warm-white key light, product colors as only accents",
        "objects": ["hero product", "reflective surface", "controlled highlight"],
    },
    "education": {
        "mood": "credible expertise, structured growth, accessible authority",
        "scene": (
            "Modern learning environment with premium notebook, quality pen, and tablet on dark wood desk. "
            "Controlled warm side lighting creating study atmosphere. "
            "Organized workspace communicating disciplined knowledge. Real photography."
        ),
        "composition": "overhead or 30-degree desk shot with organized elements and reading space on the left",
        "palette_hint": "dark wood, warm amber light, deep navy, subtle green accent for growth",
        "objects": ["notebook", "quality pen", "tablet", "warm desk lamp"],
    },
    "professional_services": {
        "mood": "executive confidence, strategic depth, consulting authority",
        "scene": (
            "Premium office detail shot — leather portfolio, fountain pen, business card on dark desk. "
            "Dramatic side lighting from floor-to-ceiling window. "
            "Executive environment communicating serious business. Real photography."
        ),
        "composition": "tight detail crop with architectural depth and clean left negative space",
        "palette_hint": "dark mahogany, black leather, warm gold accent, deep navy shadows",
        "objects": ["leather portfolio", "fountain pen", "architectural detail", "window light"],
    },
}

READY_PROMPTS: dict[str, str] = {
    "lead_capture": "Show a business losing leads due to slow follow-up, then imply an automated recovery system.",
    "offer_launch": "Present a premium service launch visual that feels urgent, polished, and conversion-oriented.",
    "testimonial": "Create a visual that suggests client proof, confidence, and visible business improvement.",
    "objection_breaker": "Depict the contrast between doing everything manually vs automating the critical bottleneck.",
    "case_study": "Show before-and-after operational clarity with a premium business transformation tone.",
    "educational_tip": "Illustrate one practical growth lesson in a clean, high-credibility social format.",
    "booking_push": "Visually nudge the viewer toward scheduling a call, demo, or consultation today.",
    "retargeting": "Create a reminder-style visual for someone who already showed interest but has not acted.",
    "seasonal_promo": "Blend seasonal commercial energy with a concrete automation or website offer.",
    "brand_authority": "Create a founder-brand visual that feels experienced, strategic, and commercially sharp.",
    "workflow_upgrade": "Show operational chaos transformed into a clean automated workflow.",
    "whatsapp_conversion": "Depict WhatsApp as a serious sales channel with immediate response and booked meetings.",
    "product_showcase": "Highlight a single hero product or service with premium studio-quality presentation.",
    "pain_point": "Visualize the specific frustration the audience experiences daily before discovering the solution.",
    "social_proof_stats": "Present impressive numbers or metrics in a clean, credible data-visualization style.",
    "urgency_scarcity": "Create time-sensitive visual tension that drives immediate action without feeling desperate.",
    "competitor_contrast": "Subtly position the brand as the premium alternative without naming competitors.",
    "onboarding_welcome": "Show a warm, professional first-contact moment that makes new clients feel valued.",
    "reactivation": "Re-engage dormant clients with a visual that combines familiarity with a fresh offer.",
    "behind_the_scenes": "Reveal authentic operational moments that build trust and humanize the brand.",
    "faq_visual": "Address the top objection or question visually with clarity and commercial confidence.",
    "event_announcement": "Create anticipation for a launch, webinar, or live event with premium energy.",
}

VERTICAL_ACCENT_MAP: dict[str, tuple[str, str]] = {
    "default": ("orange accent", "#f97316"),
    "dental": ("blue accent", "#3b82f6"),
    "restaurant": ("warm amber accent", "#eab308"),
    "restaurants": ("warm amber accent", "#eab308"),
    "realestate": ("gold accent", "#d4af37"),
    "real_estate": ("gold accent", "#d4af37"),
    "gym": ("red-orange accent", "#ef4444"),
    "beauty": ("rose accent", "#f472b6"),
    "spa": ("rose accent", "#f472b6"),
    "spas": ("rose accent", "#f472b6"),
    "ecommerce": ("electric blue accent", "#3b82f6"),
    "education": ("emerald accent", "#10b981"),
    "professional_services": ("gold accent", "#d4af37"),
}

CREATIVE_DIRECTOR: list[dict[str, Any]] = [
    {
        "key": "cinematic_photography",
        "description": "Shot on Sony A7R V, 35mm f/1.4, ISO 400, Kodak Portra 400 color grading, subtle halation, real film grain, shallow depth of field, golden hour bokeh.",
        "references": "cinematic commercial photography, premium startup campaign stills",
        "composition": "foreground subject with atmospheric depth, clean negative space for overlay on the left",
        "anti_slop": "No fake UI, no plastic skin, no warped hands, no duplicate objects, no synthetic corporate stock-photo smiles, no floating interface elements.",
    },
    {
        "key": "editorial_dark",
        "description": "Canon 5D Mark IV editorial lighting, Monocle and Wallpaper* magazine aesthetic, dramatic shadow falloff, controlled luxury color palette, tactile materials.",
        "references": "Monocle cover story, Wallpaper* business editorial",
        "composition": "magazine spread discipline, elegant asymmetry, restrained luxury negative space",
        "anti_slop": "No generic Canva composition, no cheesy startup clichés, no fake app dashboards, no oversaturated glow, no amateur poster layout.",
    },
    {
        "key": "urban_mexico",
        "description": "Fujifilm X-T5 street photography, CDMX and Guadalajara urban texture, Roma Norte, Juarez, Condesa, Americana and Providencia references, sodium vapor and neon reflections, concrete and storefront grit.",
        "references": "real Mexico City and Guadalajara night photography, independent commercial street editorial",
        "composition": "street-level perspective, motion and depth, practical negative space against architecture or signage glow",
        "anti_slop": "No cyberpunk parody, no sci-fi holograms, no generic neon tunnel, no fake Asian city cues, no tourist postcard look.",
    },
    {
        "key": "minimal_studio",
        "description": "Clean studio shot, controlled softbox lighting, high-end product photography feel, medium-format commercial precision, crisp edges, premium matte surfaces.",
        "references": "Apple-adjacent product minimalism, premium direct-response studio campaign",
        "composition": "single hero element, disciplined margins, generous negative space, minimal distractions",
        "anti_slop": "No clutter, no random props, no busy textures, no cheesy gradients, no fake 3D UI panels.",
    },
    {
        "key": "abstract_geometric",
        "description": "Pure design composition, no photography, geometric forms, restrained gradients, glassmorphism accents, premium motion-brand still frame, editorial poster discipline.",
        "references": "Swiss poster systems, premium SaaS motion-frame stills, abstract brand systems",
        "composition": "shape-led hierarchy, layered depth, clean left-side negative space for overlay text",
        "anti_slop": "No fake icons, no template blobs, no random Memphis shapes, no childish gradients, no stock illustration look.",
    },
    {
        "key": "documentary",
        "description": "Leica M11 documentary style, candid reportage photography, raw real business moment, natural available light, honest texture, restrained contrast, observational framing.",
        "references": "reportage business photography, documentary entrepreneurship stories",
        "composition": "candid lived-in moment with real context and a clean reading lane for overlay",
        "anti_slop": "No posed call-center smiles, no sterile stock office setups, no uncanny expressions, no fake teamwork tableau.",
    },
]


# ─── Anti-AI-slop prompt blocks ───────────────────────────────────────────────
# Modular strings appended to the generation prompt to eliminate the 12 "tells"
# that mark an image as AI-generated. Mix and match based on scene type.
ANTI_AI_BLOCKS: dict[str, str] = {
    "photography_real": (
        "Shot on Sony A7IV with 35mm f/1.4 GM lens. Natural available light "
        "from a single large window on the left side. ISO 400, slight grain visible. "
        "Color science: natural Sony colors, not color-graded. "
        "Subtle real-camera imperfections: micro-vignette in corners, very slight "
        "chromatic aberration on high-contrast edges, natural lens vignetting. "
        "This is an actual photograph — NOT a 3D render."
    ),
    "office_real": (
        "A REAL office desk, not a styled stock photo setup. Signs of actual work: "
        "a half-empty ceramic coffee mug (slightly chipped rim), 2-3 cables visible "
        "but organized, a small plant with one slightly yellowing leaf, a notebook "
        "with a pen on top. The desk surface has micro-scratches from daily use. "
        "NOT magazine-perfect — lived-in but organized. "
        "The wall behind has subtle plaster or painted-concrete texture, not perfectly smooth."
    ),
    "screen_realistic": (
        "CRITICAL — DEVICE SCREEN: The laptop or phone screen MUST show one of: "
        "(a) a dark-themed code editor (VS Code Dark+) with syntax-highlighted code "
        "in blues/greens/oranges on a near-black background; "
        "(b) a dark analytics dashboard with clean charts and a subtle screen glow "
        "spilling onto the keyboard; "
        "(c) the screen is OFF or CLOSED — showing only the aluminum exterior. "
        "NEVER a blank white screen, gradient screen, generic UI mockup, or "
        "floating hologram interface. "
        "The screen must cast a subtle colored light spill onto surrounding surfaces."
    ),
    "lighting_natural": (
        "LIGHTING (strict): single key light — large soft source from upper-left "
        "(window or softbox) creating gentle shadows on the right side of all objects. "
        "Fill is ambient bounce from the room, NOT a second light. "
        "Shadows are mandatory — every object casts a soft shadow. No floating objects. "
        "Light has warm color temperature (slightly amber daylight). "
        "Natural falloff: brighter near the source, dimmer toward frame edges. "
        "If a screen is on, its light spills a blue-ish tint onto nearby surfaces. "
        "NEVER flat even lighting from all directions, HDR tonemapping, or competing "
        "colored light sources."
    ),
    "color_grading": (
        "COLOR GRADING: slightly desaturated like an unedited real photo. "
        "Shadows push very slightly toward deep navy/teal (NOT purple). "
        "Highlights neutral to slightly warm. Matte finish, NOT glossy HDR. "
        "Medium contrast — not crushed blacks, not lifted shadows. "
        "Saturation pulled back 8-10% from default. "
        "ONE accent color (brand red/coral) is the only saturated element; "
        "everything else is more muted. "
        "Reference: Kodak Portra 400 or Fujifilm Pro 400H Lightroom preset — "
        "film-like, organic, slightly nostalgic."
    ),
    "texture_real": (
        "MATERIALS AND TEXTURES: "
        "Wood: visible grain variation, subtle scratches, natural knots. "
        "Metal (laptop, phone): fingerprint smudges at certain angles, micro-scratches on aluminum. "
        "Fabric: visible weave pattern, slight wrinkles, NOT perfectly pressed. "
        "Paper/notebook: slightly bent corners, pen indentations visible in raking light. "
        "Coffee mug: ceramic glaze with subtle variations, possibly a small chip. "
        "Glass: fingerprints and environment reflections visible at angles. "
        "NEVER perfectly smooth uniform surfaces or visibly tiling textures."
    ),
    "composition_editorial": (
        "COMPOSITION: rule of thirds — main subject at an intersection point, NOT dead center. "
        "Slight 1-2 degree tilt for dynamism (NOT perfectly level). "
        "Foreground element slightly blurred in nearest plane (edge of mug, corner of notebook) "
        "creating depth layers. "
        "35-45% of frame is negative space. Minimum 3 depth planes visible. "
        "Frame extends beyond edges — objects cut by frame edges naturally. "
        "NEVER perfectly centered, perfectly symmetrical, or bird's-eye view."
    ),
    "people_real": (
        "IF A PERSON APPEARS: show from behind, side profile, or hands only — "
        "AVOID full frontal face (uncanny valley risk). "
        "If hands visible: show from back/side holding device, fingers naturally wrapped. "
        "NEVER all 10 fingers splayed individually. "
        "Skin: natural texture, color variation at knuckles, NOT airbrushed. "
        "Clothing: real fabric with wrinkles, visible stitching. "
        "Body language: relaxed, natural posture, slight slouch. NOT model-posed. "
        "NEVER direct eye contact at camera, plastic skin, or fashion-model posing."
    ),
    "dark_mode_tech": (
        "DARK MODE AESTHETIC: background is NOT pure black — use very dark navy (#0a0a14) "
        "or very dark charcoal (#0d0d12) with subtle color depth and very faint texture/grain. "
        "Elements emerge from darkness with subtle backlighting or edge highlights. "
        "Any gradient in the background is BARELY perceptible — a 3-5% shift. "
        "Glow effects are subtle: screen glow barely tints surrounding surfaces. "
        "Overall feeling: premium tech office at night with only monitors on. "
        "NEVER pure black backgrounds, neon colors, tron-grid, or visible RGB lighting."
    ),
}

# Which blocks to inject for each style preset
_STYLE_ANTI_AI_MAP: dict[str, list[str]] = {
    "premium":    ["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "composition_editorial", "dark_mode_tech", "screen_realistic"],
    "default":    ["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "composition_editorial", "dark_mode_tech", "screen_realistic"],
    "editorial":  ["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "composition_editorial"],
    "bold":       ["lighting_natural", "color_grading", "dark_mode_tech", "screen_realistic"],
    "human":      ["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "people_real", "screen_realistic"],
    "data":       ["lighting_natural", "color_grading", "composition_editorial", "dark_mode_tech"],
    "clinics":    ["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "dental":     ["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "restaurants":["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "spas":       ["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "barbershops":["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "composition_editorial"],
    "real_estate":["photography_real", "lighting_natural", "color_grading", "texture_real",
                   "composition_editorial"],
    "glassmorphism_dark": ["photography_real", "lighting_natural", "color_grading", "texture_real", "dark_mode_tech", "composition_editorial"],
    "terminal_luxury": ["photography_real", "lighting_natural", "color_grading", "texture_real", "dark_mode_tech", "screen_realistic"],
    "hardware_precision": ["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "swiss_futurism": ["photography_real", "lighting_natural", "color_grading", "composition_editorial"],
    "neural_dark": ["photography_real", "lighting_natural", "color_grading", "composition_editorial", "dark_mode_tech"],
    "tactile_interface": ["photography_real", "lighting_natural", "color_grading", "texture_real"],
    "gym":                ["photography_real", "lighting_natural", "color_grading", "texture_real",
                           "composition_editorial"],
    "beauty":             ["photography_real", "lighting_natural", "color_grading", "texture_real",
                           "composition_editorial"],
    "ecommerce":          ["photography_real", "lighting_natural", "color_grading", "texture_real",
                           "composition_editorial", "dark_mode_tech"],
    "education":          ["photography_real", "lighting_natural", "color_grading", "texture_real",
                           "composition_editorial"],
    "professional_services": ["photography_real", "lighting_natural", "color_grading", "texture_real",
                              "composition_editorial"],
}


def _build_anti_ai_section(style_preset: str) -> str:
    keys = _STYLE_ANTI_AI_MAP.get(style_preset, _STYLE_ANTI_AI_MAP["default"])
    parts = [ANTI_AI_BLOCKS[k] for k in keys if k in ANTI_AI_BLOCKS]
    return " ".join(parts)


def _select_creative_direction(topic: str, vertical: str | None) -> dict[str, Any]:
    seed = f"{str(topic or '').strip().lower()}|{str(vertical or '').strip().lower()}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    index = int(digest[:8], 16) % len(CREATIVE_DIRECTOR)
    return CREATIVE_DIRECTOR[index]


def _resolve_vertical_accent(vertical: str | None) -> tuple[str, str]:
    key = str(vertical or "").strip().lower().replace(" ", "_")
    return VERTICAL_ACCENT_MAP.get(key, VERTICAL_ACCENT_MAP["default"])


RequestFn = Callable[[str, str, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any] | None]]


class GeminiProvider:
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
    ) -> bytes:
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for Gemini image generation")
        return await asyncio.to_thread(
            self._generate_sync, api_key=api_key, model=model, prompt=prompt, aspect_ratio=aspect_ratio,
        )

    def _generate_sync(self, *, api_key: str, model: str, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        image_bytes: bytes | None = None
        if "imagen" in model.lower():
            response = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    output_mime_type="image/jpeg",
                    image_size="1K",
                ),
            )
            generated_images = list(getattr(response, "generated_images", None) or [])
            if generated_images:
                image = generated_images[0].image
                image_bytes = getattr(image, "image_bytes", None) if image is not None else None
        else:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
            candidates = list(getattr(response, "candidates", None) or [])
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in list(getattr(content, "parts", None) or []):
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and getattr(inline_data, "data", None):
                        image_bytes = inline_data.data
                        break
                if image_bytes:
                    break
        if not image_bytes:
            raise RuntimeError("Gemini image generation returned no image bytes")
        return image_bytes


class FluxProvider:
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        image_size: str = "square_hd",
    ) -> bytes:
        if not api_key:
            raise RuntimeError("FAL_API_KEY is required for Flux image generation")

        headers = {
            "Authorization": f"Key {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "prompt": prompt,
            "image_size": image_size,
            "output_format": "jpeg",
        }
        run_url = f"https://fal.run/{model}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            result_resp = await client.post(run_url, headers=headers, json=payload)
            result_resp.raise_for_status()
            result_data = result_resp.json()

            image_url = ""
            images = result_data.get("images") or []
            if images and isinstance(images[0], dict):
                image_url = str(images[0].get("url") or "").strip()
            if not image_url:
                image_url = str(result_data.get("image", {}).get("url") or "").strip() if isinstance(result_data.get("image"), dict) else ""
            if not image_url:
                raise RuntimeError("Flux result did not contain an image URL")

            image_resp = await client.get(image_url)
            image_resp.raise_for_status()
            return image_resp.content


class DallEProvider:
    async def generate(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "hd",
    ) -> bytes:
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for DALL-E image generation")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "response_format": "b64_json",
        }
        import base64 as b64module
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            data_list = result.get("data") or []
            if not data_list:
                raise RuntimeError("DALL-E returned no image data")
            b64_data = data_list[0].get("b64_json") or ""
            if not b64_data:
                raise RuntimeError("DALL-E returned empty b64_json")
            return b64module.b64decode(b64_data)


class ProviderRouter:
    FLUX_STYLES = {
        "glassmorphism_dark",
        "terminal_luxury",
        "hardware_precision",
        "swiss_futurism",
        "neural_dark",
        "tactile_interface",
    }
    GEMINI_STYLES = {
        "premium",
        "editorial",
        "human",
        "documentary",
    }
    DALLE_STYLES = {
        "gym",
        "beauty",
        "ecommerce",
        "education",
        "professional_services",
    }

    def select_provider_name(self, style_preset: str) -> str:
        style_key = str(style_preset or "").strip().lower().replace(" ", "_")
        if style_key in self.FLUX_STYLES:
            return "flux"
        if style_key in self.DALLE_STYLES:
            return "dalle"
        if style_key in self.GEMINI_STYLES:
            return "gemini"
        return "gemini"

    def fallback_chain(self, style_preset: str) -> list[str]:
        """Return ordered list of providers to try for the given style."""
        primary = self.select_provider_name(style_preset)
        all_providers = ["gemini", "flux", "dalle"]
        chain = [primary] + [p for p in all_providers if p != primary]
        return chain


def build_image_prompt(
    *,
    topic: str,
    hook_text: str,
    style_preset: str = "premium",
    format: str = "square",
    vertical: str | None = None,
    content_type: str = "general",
    include_logo: bool = True,
) -> str:
    preset_key = str(style_preset or "premium").strip().lower().replace(" ", "_")
    preset = STYLE_PRESETS.get(preset_key) or STYLE_PRESETS["premium"]
    vertical_key = str(vertical or "").strip().lower().replace(" ", "_")
    vertical_preset = STYLE_PRESETS.get(vertical_key) or {}
    tech_noir_styles = {
        "glassmorphism_dark",
        "terminal_luxury",
        "hardware_precision",
        "swiss_futurism",
        "neural_dark",
        "tactile_interface",
    }
    creative_direction = None if preset_key in tech_noir_styles else _select_creative_direction(topic, vertical_key or vertical)
    scene = preset["scene"] if preset_key in tech_noir_styles else vertical_preset.get("scene", preset["scene"])
    composition = preset["composition"] if preset_key in tech_noir_styles else vertical_preset.get("composition", preset["composition"])
    palette_hint = preset["palette_hint"] if preset_key in tech_noir_styles else vertical_preset.get("palette_hint", preset["palette_hint"])
    accent_name, accent_hex = _resolve_vertical_accent(vertical_key or vertical)
    content_hint = READY_PROMPTS.get(str(content_type or "general").strip(), "")
    objects = ", ".join(preset.get("objects", []))
    visual_rules = ", ".join(BRAND["visual_rules"])
    voice = ", ".join(BRAND["voice"])
    logo_instruction = (
        "Leave subtle negative space in the bottom-right corner for a later KAN Logic wordmark overlay."
        if include_logo
        else "Do not reserve space for any logo."
    )
    dims = FORMAT_DIMENSIONS.get(format, FORMAT_DIMENSIONS["square"])
    prompt = (
        f"Create a professional {dims[0]}x{dims[1]} social media image for {BRAND['name']}. "
        f"Brand positioning: {BRAND['positioning']}. "
        f"Topic: {topic}. "
        f"Core hook theme: {hook_text}. "
        f"Style preset: {preset_key}. "
        f"Format: {format}. "
        f"Vertical: {vertical_key or 'general business'}. "
        f"Content type: {content_type}. "
        f"Mood: {preset['mood']}. "
        f"Scene: {scene}. "
        f"Composition: {composition}. "
        f"Palette direction: {palette_hint}. "
        f"Accent color rule for this vertical: {accent_name} {accent_hex}. "
        f"Suggested objects or context: {objects}. "
        f"Content objective: {content_hint or 'Create a compelling general business content visual.'} "
        f"Logo handling: {logo_instruction} "
        f"Brand colors must influence the image, especially dark base tones around {BRAND['palette']['background']} and the accent color {accent_hex}. "
        f"Tone: {voice}. "
        "Make it polished, commercial, premium, and relevant for a modern Mexican business audience. "
        "Use real photography language or premium design direction so the model thinks like a photographer or art director, not a generic image generator. "
        f"Follow these visual rules: {visual_rules}. "
        f"ANTI-AI PHOTOGRAPHY DIRECTIVES: {_build_anti_ai_section(preset_key)} "
    )
    if creative_direction is not None:
        prompt += (
            f"Creative director style: {creative_direction['key']}. "
            f"Creative brief: {creative_direction['description']}. "
            f"Reference system: {creative_direction['references']}. "
            f"Composition language: {creative_direction['composition']}. "
            f"Anti-AI-slop directives: {creative_direction['anti_slop']} "
        )
    prompt += (
        "ABSOLUTELY NO TEXT, WORDS, LETTERS, OR TYPOGRAPHY IN THE IMAGE. "
        "Pure background composition only. Any text in the image will ruin the design. "
        "No emojis, no icons, no symbols, no UI elements, no text overlays, no watermarks. "
        "leave clean space for text overlay, no text in the image. "
        "CRITICAL COLOR RULES: "
        "- The image must be 70%+ very dark (near black, #0a0a14). "
        "- Color accents appear on SMALL elements only: a LED light, a screen glow, a reflection, a small object. "
        "- The accent color occupies maximum 10-15% of the image area. "
        "- NEVER a monochromatic teal/cyan/blue wash over everything. "
        "- Think: dark room with one small source of colored light. "
        "- The darkness is the design. The accent is the punctuation. "
        "DO NOT generate any text, words, letters, or numbers in the image. "
        "Leave clean dark area in left side for text overlay. "
        "NOT a 3D render. NOT CGI. A REAL photograph. "
        "Subtle film grain. Natural lens imperfections. "
        "Single light source with consistent shadows."
    )
    return prompt


class AssetManager:
    def __init__(
        self,
        *,
        requester: RequestFn | None = None,
        gemini_provider: "GeminiProvider | None" = None,
        flux_provider: "FluxProvider | None" = None,
        dalle_provider: "DallEProvider | None" = None,
        provider_router: "ProviderRouter | None" = None,
    ) -> None:
        self.requester = requester
        self.gemini_provider = gemini_provider or GeminiProvider()
        self.flux_provider = flux_provider or FluxProvider()
        self.dalle_provider = dalle_provider or DallEProvider()
        self.provider_router = provider_router or ProviderRouter()

    def _cloud_name(self) -> str:
        return str(os.getenv("CLOUDINARY_CLOUD_NAME") or "").strip()

    def _api_key(self) -> str:
        return str(os.getenv("CLOUDINARY_API_KEY") or "").strip()

    def _api_secret(self) -> str:
        return str(os.getenv("CLOUDINARY_API_SECRET") or "").strip()

    def _google_api_key(self) -> str:
        return str(os.getenv("GOOGLE_API_KEY") or "").strip()

    def _fal_api_key(self) -> str:
        return str(os.getenv("FAL_API_KEY") or "").strip()

    def _gemini_image_model(self) -> str:
        return str(os.getenv("GEMINI_MODEL") or _DEFAULT_GEMINI_IMAGE_MODEL).strip()

    def _fal_model(self) -> str:
        return str(os.getenv("FAL_MODEL") or _DEFAULT_FAL_MODEL).strip()

    def _openai_api_key(self) -> str:
        return str(os.getenv("OPENAI_API_KEY") or "").strip()

    def _dalle_model(self) -> str:
        return str(os.getenv("DALLE_MODEL") or _DEFAULT_OPENAI_IMAGE_MODEL).strip()

    def get_ready_prompt(self, key: str) -> str:
        ready = READY_PROMPTS.get(str(key).strip())
        if not ready:
            raise KeyError(f"Unknown prompt_key: {key}")
        return ready

    def _strip_emojis(self, value: str) -> str:
        cleaned = _EMOJI_RE.sub("", str(value or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _signature(self, *, public_id: str, timestamp: int) -> str:
        api_secret = self._api_secret()
        if not api_secret:
            raise RuntimeError("CLOUDINARY_API_SECRET is required")
        payload = f"public_id={public_id}&timestamp={timestamp}{api_secret}"
        return hashlib.sha1(payload.encode()).hexdigest()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.requester is not None:
            return await self.requester(method, url, data, files)

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method.upper(), url, data=data, files=files)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    async def upload_image(self, image_path: str) -> str:
        cloud_name = self._cloud_name()
        api_key = self._api_key()
        api_secret = self._api_secret()
        if not cloud_name or not api_key or not api_secret:
            raise RuntimeError(
                "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET are required"
            )

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        timestamp = int(time())
        public_id = path.stem
        signature = self._signature(public_id=public_id, timestamp=timestamp)
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        data = {
            "api_key": api_key,
            "timestamp": str(timestamp),
            "public_id": public_id,
            "signature": signature,
        }
        with path.open("rb") as fh:
            payload = await self._request(
                "POST",
                url,
                data=data,
                files={"file": (path.name, fh, "image/jpeg")},
            )
        secure_url = str((payload or {}).get("secure_url") or "").strip()
        if not secure_url:
            raise RuntimeError("Cloudinary upload did not return secure_url")
        return secure_url

    def _resolve_vertical(self, vertical: str | None = None, fallback: str | None = None) -> str:
        raw = str(vertical or fallback or "default").strip().lower().replace(" ", "_")
        aliases = {
            "clinic": "clinics",
            "medical": "clinics",
            "dentist": "dental",
            "dental": "dental",
            "restaurant": "restaurants",
            "spa": "spas",
            "barber": "barbershops",
            "fitness": "gym",
            "gimnasio": "gym",
            "salon": "beauty",
            "belleza": "beauty",
            "tienda": "ecommerce",
            "shop": "ecommerce",
            "escuela": "education",
            "academia": "education",
            "consulting": "professional_services",
            "consultoria": "professional_services",
            "despacho": "professional_services",
        }
        normalized = aliases.get(raw, raw)
        return normalized if normalized in STYLE_PRESETS else "default"

    def _build_gemini_prompt(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        format: str = "square",
        vertical: str | None = None,
        content_type: str = "general",
        include_logo: bool = True,
    ) -> str:
        return build_image_prompt(
            topic=str(topic or "").strip(),
            hook_text=str(hook_text or "").strip(),
            style_preset=style_preset,
            format=format,
            vertical=self._resolve_vertical(vertical),
            content_type=content_type,
            include_logo=include_logo,
        )

    def _font_cache_path(self, filename: str) -> Path:
        return _FONT_ASSETS_DIR / filename

    def _font_tmp_fallback_path(self, filename: str) -> Path:
        return Path(gettempdir()) / filename

    def _ensure_downloaded_font(self, *, urls: tuple[str, ...], filename: str) -> Path | None:
        primary_path = self._font_cache_path(filename)
        fallback_path = self._font_tmp_fallback_path(filename)
        for existing in (primary_path, fallback_path):
            if existing.exists():
                return existing
        if filename in _FONT_DOWNLOAD_ATTEMPTED:
            return None
        _FONT_DOWNLOAD_ATTEMPTED.add(filename)
        _FONT_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        for url in urls:
            for target in (primary_path, fallback_path):
                try:
                    response = httpx.get(url, timeout=10.0, follow_redirects=True)
                    response.raise_for_status()
                    target.write_bytes(response.content)
                    return target
                except Exception:
                    continue
        logger.warning("Could not download font %s; using fallback font", filename)
        return None

    def _ensure_sora_bold_font(self) -> Path | None:
        return self._ensure_downloaded_font(urls=_SORA_BOLD_URLS, filename=_SORA_FONT_NAME)

    def _ensure_jetbrains_mono_font(self) -> Path | None:
        return self._ensure_downloaded_font(urls=_JETBRAINS_MONO_URLS, filename=_JETBRAINS_MONO_NAME)

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_path = self._ensure_sora_bold_font() if bold else None
        if font_path is not None:
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                logger.exception("Could not load downloaded Sora Bold font")
        fallback_candidates = [
            "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        ]
        for candidate in fallback_candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _load_mono_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_path = self._ensure_jetbrains_mono_font()
        if font_path is not None:
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                logger.exception("Could not load downloaded JetBrains Mono font")
        fallback_candidates = [
            "DejaVuSansMono.ttf",
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/System/Library/Fonts/Supplemental/Menlo.ttc",
        ]
        for candidate in fallback_candidates:
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _render_with_pillow(self, output: Path, format: str = "square") -> None:
        dims = FORMAT_DIMENSIONS.get(format, FORMAT_DIMENSIONS["square"])
        image = Image.new("RGB", dims, color=_BACKGROUND)
        draw = ImageDraw.Draw(image)
        w, h = dims
        margin = int(min(w, h) * 0.074)
        draw.rounded_rectangle(
            (margin, margin, w - margin, h - margin),
            radius=36, outline=_ACCENT, width=8,
        )
        draw.rectangle((margin + 40, margin + 40, margin + 140, margin + 140), fill=_ACCENT)

        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="JPEG", quality=92, optimize=True)

    def _apply_text_background_pill(
        self,
        image: Image.Image,
        *,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> Image.Image:
        base = image.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        mask = Image.new("L", base.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rectangle((left, top, right, bottom), fill=int(255 * 0.35))
        blurred_mask = mask.filter(ImageFilter.GaussianBlur(radius=20))
        overlay.paste((0, 0, 0, 255), (0, 0), blurred_mask)
        return Image.alpha_composite(base, overlay)

    def _measure_line_width(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        letter_spacing: float,
    ) -> float:
        if not text:
            return 0.0
        width = 0.0
        for index, char in enumerate(text):
            width += float(draw.textlength(char, font=font))
            if index < len(text) - 1:
                width += letter_spacing
        return width

    def _measure_multiline_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        lines: list[str],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        line_spacing: int,
        letter_spacing: float,
    ) -> tuple[int, int]:
        widths = [
            self._measure_line_width(draw=draw, text=line, font=font, letter_spacing=letter_spacing)
            for line in lines
        ] or [0.0]
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        line_height = max(1, bbox[3] - bbox[1])
        total_height = line_height * len(lines)
        if len(lines) > 1:
            total_height += line_spacing * (len(lines) - 1)
        return int(max(widths)), int(total_height)

    def _wrap_hook_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        hook_text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
        letter_spacing: float,
    ) -> list[str]:
        words = hook_text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            candidate_width = self._measure_line_width(
                draw=draw,
                text=candidate,
                font=font,
                letter_spacing=letter_spacing,
            )
            if candidate_width <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _fit_hook_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        hook_text: str,
        max_width: int,
        max_height: int,
        letter_spacing: float,
    ) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont, int, tuple[int, int, int, int]]:
        for font_size in range(80, 31, -2):
            font = self._load_font(font_size, bold=True)
            line_spacing = max(8, int(font_size * 0.15))
            lines = self._wrap_hook_text(
                draw=draw,
                hook_text=hook_text,
                font=font,
                max_width=max_width,
                letter_spacing=letter_spacing,
            )
            text_width, text_height = self._measure_multiline_text(
                draw=draw,
                lines=lines,
                font=font,
                line_spacing=line_spacing,
                letter_spacing=letter_spacing,
            )
            if text_width <= max_width and text_height <= max_height:
                return lines, font, line_spacing, (0, 0, text_width, text_height)

        font = self._load_font(32, bold=True)
        line_spacing = max(8, int(32 * 0.15))
        lines = self._wrap_hook_text(
            draw=draw,
            hook_text=hook_text,
            font=font,
            max_width=max_width,
            letter_spacing=letter_spacing,
        )
        text_width, text_height = self._measure_multiline_text(
            draw=draw,
            lines=lines,
            font=font,
            line_spacing=line_spacing,
            letter_spacing=letter_spacing,
        )
        return lines, font, line_spacing, (0, 0, text_width, text_height)

    def _draw_spaced_multiline_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        lines: list[str],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
        line_spacing: int,
        letter_spacing: float,
    ) -> None:
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        line_height = max(1, bbox[3] - bbox[1])
        cursor_y = y
        for line in lines:
            cursor_x = x
            for index, char in enumerate(line):
                draw.text((cursor_x, cursor_y), char, fill=fill, font=font)
                cursor_x += float(draw.textlength(char, font=font))
                if index < len(line) - 1:
                    cursor_x += letter_spacing
            cursor_y += line_height + line_spacing

    def _overlay_text_and_wordmark(
        self,
        *,
        hook_text: str,
        vertical: str | None,
        include_logo: bool,
        image_path: Path,
        format: str = "square",
    ) -> None:
        cleaned_hook = self._strip_emojis(hook_text)
        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        width, height = image.size
        fmt = str(format or "square").strip().lower()
        # Adapt layout for tall formats (story/reel) vs wide (landscape)
        if fmt in ("story", "reel"):
            padding = 50
            text_area_width = int(width * 0.85)
            text_area_height = int(height * 0.25)
        elif fmt == "landscape":
            padding = 70
            text_area_width = int(width * 0.50)
            text_area_height = int(height * 0.50)
        else:
            padding = 60
            text_area_width = int(width * 0.65)
            text_area_height = int(height * 0.42)
        letter_spacing = 0.5
        accent_color = _resolve_vertical_accent(vertical)[1]
        hook_lines, title_font, line_spacing, hook_bbox = self._fit_hook_text(
            draw=draw,
            hook_text=cleaned_hook,
            max_width=text_area_width,
            max_height=text_area_height,
            letter_spacing=letter_spacing,
        )
        hook_x = padding
        accent_y = padding + 10
        accent_width = 60
        accent_height = 3
        hook_y = accent_y + accent_height + 16
        pill_left = hook_x - 40
        pill_top = max(0, accent_y - 30)
        pill_right = min(width, hook_x + hook_bbox[2] + 40)
        pill_bottom = min(height, hook_y + hook_bbox[3] + 30)
        image = self._apply_text_background_pill(
            image,
            left=pill_left,
            top=pill_top,
            right=pill_right,
            bottom=pill_bottom,
        )
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            (hook_x, accent_y, hook_x + accent_width, accent_y + accent_height),
            fill=accent_color,
        )
        self._draw_spaced_multiline_text(
            draw=draw,
            x=hook_x,
            y=hook_y,
            lines=hook_lines,
            font=title_font,
            fill=_TEXT,
            line_spacing=line_spacing,
            letter_spacing=letter_spacing,
        )

        if include_logo:
            wordmark = "KAN Logic"
            brand_font = self._load_mono_font(13)
            bbox = draw.textbbox((0, 0), wordmark, font=brand_font)
            wordmark_width = bbox[2] - bbox[0]
            wordmark_height = bbox[3] - bbox[1]
            pad_x = 14
            pad_y = 8
            accent_bar_width = 2
            accent_gap = 10
            badge_width = wordmark_width + (pad_x * 2) + accent_bar_width + accent_gap
            badge_height = wordmark_height + (pad_y * 2)
            mark_x = width - badge_width - 54
            mark_y = height - badge_height - 44
            badge = Image.new("RGBA", image.size, (0, 0, 0, 0))
            badge_draw = ImageDraw.Draw(badge)
            badge_draw.rounded_rectangle(
                (mark_x, mark_y, mark_x + badge_width, mark_y + badge_height),
                radius=6,
                fill=(10, 10, 20, int(255 * 0.8)),
                outline=(26, 26, 46, 255),
                width=1,
            )
            line_left = mark_x + pad_x
            line_top = mark_y + pad_y
            line_bottom = mark_y + badge_height - pad_y
            badge_draw.rectangle(
                (line_left, line_top, line_left + accent_bar_width, line_bottom),
                fill=accent_color,
            )
            image = Image.alpha_composite(image.convert("RGBA"), badge).convert("RGB")
            draw = ImageDraw.Draw(image)
            text_x = line_left + accent_bar_width + accent_gap
            text_y = mark_y + pad_y - 1
            draw.text((text_x, text_y), wordmark, fill="#8888a0", font=brand_font)
        image.save(image_path, format="JPEG", quality=92, optimize=True)

    # Style-aware post-processing profiles
    _POST_PROCESSING_PROFILES: dict[str, dict[str, float]] = {
        "default":      {"saturation": 0.88, "contrast": 1.15, "brightness": 0.87, "grain": 0.06, "vignette": 0.19},
        "editorial":    {"saturation": 0.82, "contrast": 1.20, "brightness": 0.85, "grain": 0.08, "vignette": 0.22},
        "documentary":  {"saturation": 0.80, "contrast": 1.18, "brightness": 0.84, "grain": 0.09, "vignette": 0.20},
        "human":        {"saturation": 0.85, "contrast": 1.12, "brightness": 0.88, "grain": 0.07, "vignette": 0.16},
        "beauty":       {"saturation": 0.92, "contrast": 1.08, "brightness": 0.90, "grain": 0.03, "vignette": 0.12},
        "gym":          {"saturation": 0.85, "contrast": 1.22, "brightness": 0.83, "grain": 0.07, "vignette": 0.24},
        "ecommerce":    {"saturation": 0.90, "contrast": 1.10, "brightness": 0.89, "grain": 0.04, "vignette": 0.14},
        "education":    {"saturation": 0.90, "contrast": 1.12, "brightness": 0.88, "grain": 0.05, "vignette": 0.15},
        "glassmorphism_dark": {"saturation": 0.85, "contrast": 1.18, "brightness": 0.82, "grain": 0.05, "vignette": 0.22},
        "terminal_luxury":    {"saturation": 0.83, "contrast": 1.20, "brightness": 0.80, "grain": 0.06, "vignette": 0.25},
    }

    def _get_post_processing_profile(self, style_preset: str) -> dict[str, float]:
        key = str(style_preset or "").strip().lower().replace(" ", "_")
        return self._POST_PROCESSING_PROFILES.get(key, self._POST_PROCESSING_PROFILES["default"])

    def _apply_post_processing(self, image_path: Path, style_preset: str = "default") -> None:
        """
        Film-photography post-processing that kills the "AI look".
        Parameters adapt per style_preset for optimal results:
          1. Desaturate (AI models over-saturate)
          2. Boost contrast (stronger shadow/highlight separation)
          3. Darken overall brightness
          4. Film grain (monochromatic noise, stronger on midtones)
          5. Pronounced vignette (natural lens falloff)
        """
        profile = self._get_post_processing_profile(style_preset)
        img = Image.open(image_path).convert("RGB")

        # 1. Desaturate
        img = ImageEnhance.Color(img).enhance(profile["saturation"])

        # 2. Contrast
        img = ImageEnhance.Contrast(img).enhance(profile["contrast"])

        # 3. Darken
        img = ImageEnhance.Brightness(img).enhance(profile["brightness"])

        # 4. Film grain — more visible on midtones, less on shadows/highlights
        arr = np.array(img, dtype=np.float32)
        lum = arr.mean(axis=2, keepdims=True) / 255.0
        grain_mask = 1.0 - np.abs(lum - 0.5) * 2.0
        grain_mask = np.clip(grain_mask, 0.3, 1.0)
        noise = np.random.normal(0, profile["grain"] * 255, (img.height, img.width, 1))
        noise = np.repeat(noise, 3, axis=2)
        arr = np.clip(arr + noise * grain_mask, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

        # 5. Vignette — radial gradient darkening toward corners
        width, height = img.size
        cx, cy = width / 2.0, height / 2.0
        max_dist = np.sqrt(cx**2 + cy**2)
        ys, xs = np.mgrid[0:height, 0:width]
        dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max_dist
        vig_strength = np.clip((dist - 0.55) / 0.45, 0.0, 1.0) ** 2 * profile["vignette"]
        vig_alpha = np.clip((1.0 - vig_strength) * 255, 0, 255).astype(np.uint8)
        vignette = Image.fromarray(vig_alpha, mode="L")
        dark = Image.new("RGB", (width, height), (0, 0, 0))
        img = Image.composite(img, dark, vignette)

        img.save(image_path, format="JPEG", quality=92, optimize=True)

    async def _generate_with_gemini(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        prompt = self._build_gemini_prompt(
            topic=topic,
            hook_text=hook_text,
            style_preset=style_preset,
            format=format,
            vertical=vertical,
            content_type=content_type,
            include_logo=include_logo,
        )
        aspect_ratio = FORMAT_ASPECT_RATIOS.get(format, "1:1")
        image_bytes = await self.gemini_provider.generate(
            api_key=self._google_api_key(),
            model=self._gemini_image_model(),
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    async def _generate_with_flux(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        prompt = self._build_gemini_prompt(
            topic=topic,
            hook_text=hook_text,
            style_preset=style_preset,
            format=format,
            vertical=vertical,
            content_type=content_type,
            include_logo=include_logo,
        )
        fal_size = _FAL_SIZE_MAP.get(format, "square_hd")
        image_bytes = await self.flux_provider.generate(
            api_key=self._fal_api_key(),
            model=self._fal_model(),
            prompt=prompt,
            image_size=fal_size,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    async def _generate_with_dalle(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        prompt = self._build_gemini_prompt(
            topic=topic,
            hook_text=hook_text,
            style_preset=style_preset,
            format=format,
            vertical=vertical,
            content_type=content_type,
            include_logo=include_logo,
        )
        dalle_size = _DALLE_SIZE_MAP.get(format, "1024x1024")
        image_bytes = await self.dalle_provider.generate(
            api_key=self._openai_api_key(),
            model=self._dalle_model(),
            prompt=prompt,
            size=dalle_size,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    async def _call_provider_with_retry(
        self,
        provider_name: str,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        generators = {
            "gemini": self._generate_with_gemini,
            "flux": self._generate_with_flux,
            "dalle": self._generate_with_dalle,
        }
        gen_fn = generators.get(provider_name, self._generate_with_gemini)
        kwargs = dict(
            topic=topic, hook_text=hook_text, style_preset=style_preset,
            format=format, vertical=vertical, content_type=content_type,
            include_logo=include_logo, output=output,
        )
        last_exc: Exception | None = None
        for attempt in range(_MAX_PROVIDER_RETRIES + 1):
            try:
                return await gen_fn(**kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_PROVIDER_RETRIES:
                    wait = _RETRY_BACKOFF_SECONDS[attempt] if attempt < len(_RETRY_BACKOFF_SECONDS) else 5
                    logger.warning(
                        "%s generation attempt %d failed for %s — retry in %ds: %s",
                        provider_name, attempt + 1, output.stem, wait, exc,
                    )
                    await asyncio.sleep(wait)
        raise last_exc  # type: ignore[misc]

    async def _generate_background(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str,
        format: str,
        vertical: str | None,
        content_type: str,
        include_logo: bool,
        output: Path,
    ) -> Path:
        chain = self.provider_router.fallback_chain(style_preset)
        kwargs = dict(
            topic=topic, hook_text=hook_text, style_preset=style_preset,
            format=format, vertical=vertical, content_type=content_type,
            include_logo=include_logo, output=output,
        )
        for i, provider_name in enumerate(chain):
            try:
                return await self._call_provider_with_retry(provider_name, **kwargs)
            except Exception:
                if i < len(chain) - 1:
                    logger.exception(
                        "%s generation failed for %s; falling back to %s",
                        provider_name, output.stem, chain[i + 1],
                    )
                else:
                    logger.exception(
                        "All providers failed for %s; last was %s", output.stem, provider_name,
                    )
                    raise

    async def generate_post_image(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        format: str = "square",
        vertical: str | None = None,
        content_type: str = "general",
        include_logo: bool = True,
        asset_id: str | None = None,
    ) -> str:
        output_id = str(asset_id or hashlib.sha1(f"{topic}|{hook_text}|{style_preset}|{format}|{vertical}|{content_type}|{include_logo}".encode()).hexdigest()[:16]).strip()
        output = Path(gettempdir()) / f"{output_id}.jpg"
        try:
            await self._generate_background(
                topic=topic,
                hook_text=hook_text,
                style_preset=style_preset,
                format=format,
                vertical=vertical,
                content_type=content_type,
                include_logo=include_logo,
                output=output,
            )
            logger.info("Generated post image at %s", output)
        except Exception:
            logger.exception("All providers failed for %s; falling back to Pillow", output_id)
            self._render_with_pillow(output)
            logger.info("Generated post image with Pillow fallback at %s", output)
        try:
            self._apply_post_processing(output, style_preset=style_preset)
        except Exception:
            logger.exception("Post-processing failed for %s; skipping", output_id)
        try:
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                vertical=vertical,
                include_logo=include_logo,
                image_path=output,
                format=format,
            )
        except Exception:
            logger.exception("Image overlay failed for %s; rebuilding with Pillow background", output_id)
            self._render_with_pillow(output)
            try:
                self._apply_post_processing(output, style_preset=style_preset)
            except Exception:
                logger.exception("Post-processing failed during overlay recovery for %s; skipping", output_id)
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                vertical=vertical,
                include_logo=include_logo,
                image_path=output,
                format=format,
            )
        return await self.upload_image(str(output))

    async def generate_post_image_bytes(
        self,
        *,
        topic: str,
        hook_text: str,
        style_preset: str = "premium",
        format: str = "square",
        vertical: str | None = None,
        content_type: str = "general",
        include_logo: bool = True,
        asset_id: str | None = None,
        custom_prompt: str | None = None,
    ) -> Any:
        """
        Genera imagen y retorna ImageResult(bytes, prompt_used) sin subir a Cloudinary.
        Usado por el QC pipeline para revisar antes de publicar.
        """
        from brain.agents.quality_control_agent import ImageResult

        output_id = str(
            asset_id
            or hashlib.sha1(
                f"{topic}|{hook_text}|{style_preset}|{format}|{vertical}|{content_type}|{include_logo}".encode()
            ).hexdigest()[:16]
        ).strip()
        output = Path(gettempdir()) / f"{output_id}.jpg"
        effective_topic = custom_prompt or topic

        try:
            await self._generate_background(
                topic=effective_topic,
                hook_text=hook_text,
                style_preset=style_preset,
                format=format,
                vertical=vertical,
                content_type=content_type,
                include_logo=include_logo,
                output=output,
            )
        except Exception:
            logger.exception("All providers failed for QC pipeline; falling back to Pillow")
            self._render_with_pillow(output)

        try:
            self._apply_post_processing(output, style_preset=style_preset)
        except Exception:
            logger.exception("Post-processing failed in QC pipeline; skipping")

        try:
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                vertical=vertical,
                include_logo=include_logo,
                image_path=output,
                format=format,
            )
        except Exception:
            logger.exception("Overlay failed in QC pipeline; rebuilding with Pillow")
            self._render_with_pillow(output)
            try:
                self._apply_post_processing(output, style_preset=style_preset)
            except Exception:
                logger.exception("Post-processing failed in QC pipeline recovery; skipping")
            self._overlay_text_and_wordmark(
                hook_text=hook_text,
                vertical=vertical,
                include_logo=include_logo,
                image_path=output,
                format=format,
            )

        return ImageResult(image=output.read_bytes(), prompt_used=effective_topic, asset_id=output_id)

    async def upload_image_bytes(self, image: bytes, asset_id: str) -> str:
        """Sube bytes directamente a Cloudinary. Usado por el QC pipeline post-aprobación."""
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image)
            tmp_path = tmp.name
        try:
            return await self.upload_image(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
