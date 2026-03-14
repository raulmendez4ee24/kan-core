import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceAsset
from tools.supabase_storage import create_signed_url, upload_bytes


def _enabled() -> bool:
    return os.getenv("ENABLE_EVIDENCE_STORAGE", "false").lower() in {"1", "true", "yes"}


def _bucket() -> str:
    return (os.getenv("SUPABASE_EVIDENCE_BUCKET") or "evidence").strip() or "evidence"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_path(
    *,
    organization_id: UUID,
    run_id: UUID,
    step_id: Optional[UUID],
    kind: str,
    ts: datetime,
) -> str:
    safe_kind = str(kind or "evidence").strip().lower().replace(" ", "_")
    ts_slug = ts.strftime("%Y%m%dT%H%M%SZ")
    base = f"evidence/{organization_id}/{run_id}"
    if step_id:
        return f"{base}/{step_id}/{safe_kind}_{ts_slug}.png"
    return f"{base}/{safe_kind}_{ts_slug}.png"


def _extract_png_size(png_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
    try:
        image = Image.open(io := __import__("io").BytesIO(png_bytes))
        return int(image.width), int(image.height)
    except Exception:
        return None, None


async def persist_evidence_png(
    session: AsyncSession,
    *,
    organization_id: UUID,
    run_id: UUID,
    step_id: Optional[UUID],
    kind: str,
    png_bytes: bytes,
    redacted: bool = False,
    expires_at: Optional[datetime] = None,
) -> Optional[EvidenceAsset]:
    """Upload PNG to object storage and persist EvidenceAsset row.

    Returns None when evidence storage is disabled/unconfigured.
    """
    if not _enabled():
        return None
    if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
        return None

    ts = datetime.now(timezone.utc)
    bucket = _bucket()
    path = _build_path(
        organization_id=organization_id,
        run_id=run_id,
        step_id=step_id,
        kind=kind,
        ts=ts,
    )

    ok = await upload_bytes(bucket=bucket, path=path, data=bytes(png_bytes), content_type="image/png", upsert=True)
    if not ok:
        return None

    width, height = _extract_png_size(bytes(png_bytes))
    asset = EvidenceAsset(
        organization_id=organization_id,
        run_id=run_id,
        step_id=step_id,
        kind=str(kind or "screenshot"),
        mime_type="image/png",
        bucket=bucket,
        path=path,
        sha256=_sha256(bytes(png_bytes)),
        size_bytes=int(len(png_bytes)),
        width=width,
        height=height,
        redacted=bool(redacted),
        expires_at=expires_at,
        created_at=ts,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


async def create_asset_signed_url(
    session: AsyncSession,
    *,
    organization_id: UUID,
    asset_id: UUID,
    expires_seconds: int = 600,
) -> Optional[str]:
    if not _enabled():
        return None
    row = (
        await session.execute(
            select(EvidenceAsset).where(
                EvidenceAsset.organization_id == organization_id,
                EvidenceAsset.id == asset_id,
            )
        )
    ).scalars().first()
    if not row:
        return None
    return await create_signed_url(bucket=row.bucket, path=row.path, expires_in=int(expires_seconds))


def decode_image_base64(payload: Dict[str, Any]) -> Optional[bytes]:
    """Extract PNG bytes from desktop-agent result payload."""
    if not isinstance(payload, dict):
        return None
    b64 = payload.get("image_base64")
    if not isinstance(b64, str) or not b64.strip():
        return None
    try:
        return base64.b64decode(b64.encode())
    except Exception:
        return None

