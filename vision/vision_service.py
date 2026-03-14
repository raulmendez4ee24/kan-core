import asyncio
import base64
import io
import logging
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from PIL import Image
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from execution.desktop_executor import send_to_desktop_agent
from models import DesktopCommand, DesktopCommandResult
from vision.schemas import (
    ScreenElementDetection,
    VisionAnalyzeRequest,
    VisionAnalyzeResponse,
)
from vision.screen_analyzer import VisionAnalyzerError, analyze_screen

logger = logging.getLogger("kan_core.vision.service")


class VisionServiceError(RuntimeError):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _parse_uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise VisionServiceError(f"Invalid {field}", status_code=400) from exc


def _decode_image_base64(image_base64: str) -> bytes:
    try:
        return base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise VisionServiceError("image_base64 is not valid base64", status_code=400) from exc


def _image_size(image_bytes: bytes) -> Tuple[int, int]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise VisionServiceError("Invalid image payload", status_code=400) from exc
    return int(image.width), int(image.height)


def _to_detection(row: Dict[str, Any]) -> ScreenElementDetection:
    return ScreenElementDetection.model_validate(row)


class VisionService:
    async def _load_latest_screenshot(
        self,
        *,
        session: AsyncSession,
        organization_id: UUID,
        agent_id: Optional[str],
    ) -> Tuple[bytes, str]:
        stmt = (
            select(DesktopCommandResult.data)
            .join(
                DesktopCommand,
                DesktopCommand.id == DesktopCommandResult.command_id,
            )
            .where(
                DesktopCommand.organization_id == organization_id,
                DesktopCommand.action == "take_screenshot",
                DesktopCommandResult.status == "success",
            )
            .order_by(desc(DesktopCommandResult.received_at))
            .limit(1)
        )

        if agent_id:
            agent_uuid = _parse_uuid(agent_id, field="agent_id")
            stmt = stmt.where(DesktopCommand.agent_id == agent_uuid)

        payload = (await session.execute(stmt)).scalars().first()
        if not isinstance(payload, dict):
            raise VisionServiceError(
                "No screenshot available. Run take_screenshot first.",
                status_code=404,
            )

        image_base64 = payload.get("image_base64")
        if not isinstance(image_base64, str) or not image_base64.strip():
            raise VisionServiceError(
                "Latest screenshot payload is invalid",
                status_code=502,
            )

        mime_type = str(payload.get("mime_type") or "image/png")
        return _decode_image_base64(image_base64), mime_type

    async def analyze(
        self,
        *,
        session: AsyncSession,
        client_id: str,
        request: VisionAnalyzeRequest,
    ) -> VisionAnalyzeResponse:
        organization_id = _parse_uuid(client_id, field="client_id")

        source: str
        if request.image_base64:
            image_bytes = _decode_image_base64(request.image_base64)
            image_mime_type = request.image_mime_type
            source = "request"
        else:
            image_bytes, image_mime_type = await self._load_latest_screenshot(
                session=session,
                organization_id=organization_id,
                agent_id=request.agent_id,
            )
            source = "desktop_agent_history"

        _image_size(image_bytes)

        try:
            analysis = await asyncio.to_thread(
                analyze_screen,
                image_bytes,
                request.instruction,
                image_mime_type=image_mime_type,
            )
        except VisionAnalyzerError as exc:
            logger.warning("Vision analyzer failed: %s", exc)
            raise VisionServiceError(str(exc), status_code=502) from exc
        except Exception as exc:
            logger.exception("Unexpected vision analyzer failure")
            raise VisionServiceError("Unexpected vision analyzer failure", status_code=502) from exc

        detections_raw = analysis.get("detections") or []
        detections = [
            _to_detection(item)
            for item in detections_raw
            if isinstance(item, dict)
        ]
        if not detections:
            detections = [
                ScreenElementDetection(
                    element=str(analysis.get("element") or "target_element"),
                    x=int(analysis.get("x") or 0),
                    y=int(analysis.get("y") or 0),
                    confidence=float(analysis.get("confidence") or 0.0),
                )
            ]

        primary = detections[0]
        image_width = int(analysis.get("image_width") or 0)
        image_height = int(analysis.get("image_height") or 0)

        click_result: Dict[str, Any] = {}
        if request.auto_click:
            click_parameters: Dict[str, Any] = {
                "x": primary.x,
                "y": primary.y,
                "button": request.click_button,
                "clicks": request.clicks,
            }
            if request.agent_id:
                click_parameters["agent_id"] = request.agent_id

            click_result = await send_to_desktop_agent(
                client_id=client_id,
                action="click",
                parameters=click_parameters,
                metadata={
                    "source": "vision.analyze",
                    "instruction": request.instruction,
                    "element": primary.element,
                    "confidence": primary.confidence,
                    "agent_id": request.agent_id,
                },
            )

        return VisionAnalyzeResponse(
            x=primary.x,
            y=primary.y,
            element=primary.element,
            confidence=primary.confidence,
            image_width=image_width if image_width > 0 else 1,
            image_height=image_height if image_height > 0 else 1,
            source=source,  # type: ignore[arg-type]
            command_created=bool(click_result.get("accepted")),
            command_id=(str(click_result.get("command_id")) if click_result.get("command_id") else None),
            command_status=(str(click_result.get("status")) if click_result.get("status") else None),
            detections=(detections if request.include_all_detections else []),
        )
