from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from observability import capture_exception
from security import require_client_token
from vision.schemas import VisionAnalyzeRequest, VisionAnalyzeResponse
from vision.vision_service import VisionService, VisionServiceError

router = APIRouter(prefix="/vision", tags=["vision"])


@router.post("/analyze", response_model=VisionAnalyzeResponse)
async def analyze_vision(
    req: VisionAnalyzeRequest,
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    service = VisionService()
    try:
        return await service.analyze(
            session=session,
            client_id=client_id,
            request=req,
        )
    except VisionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        capture_exception(exc, {"source": "vision.analyze", "client_id": client_id})
        raise HTTPException(status_code=502, detail="Vision analysis failed") from exc
