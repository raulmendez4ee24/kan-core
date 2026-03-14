from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brain.business_mentor import BusinessMentor
from security import require_client_token

router = APIRouter(prefix="/mentor", tags=["mentor"])


class MentorAskRequest(BaseModel):
    question: str


@router.post("/ask")
async def mentor_ask(
    req: MentorAskRequest,
    _: str = Depends(require_client_token),
):
    try:
        answer = await BusinessMentor().answer_question(req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"answer": answer}
