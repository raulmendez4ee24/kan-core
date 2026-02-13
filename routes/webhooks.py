import json
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from brain.dispatcher import handle_client_event, handle_meta_event
from observability import capture_exception
from schemas.meta import MetaWebhook
from security import require_client_token, verify_meta_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/meta", response_class=PlainTextResponse)
async def meta_verify(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    expected = os.getenv("META_VERIFY_TOKEN")
    if hub_mode != "subscribe" or not expected or hub_verify_token != expected:
        raise HTTPException(status_code=403, detail="Verification failed")
    if not hub_challenge:
        raise HTTPException(status_code=400, detail="Missing hub.challenge")
    return hub_challenge


@router.post("/meta")
async def meta_receive(request: Request, background_tasks: BackgroundTasks):
    try:
        raw_body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_meta_signature(raw_body, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        try:
            if hasattr(MetaWebhook, "model_validate"):
                model = MetaWebhook.model_validate(payload)
                validated = model.model_dump()
            else:
                model = MetaWebhook.parse_obj(payload)
                validated = model.dict()
        except ValidationError:
            raise HTTPException(status_code=400, detail="Invalid payload")

        background_tasks.add_task(handle_meta_event, validated)
        return {"status": "accepted"}
    except HTTPException:
        raise
    except Exception as exc:
        capture_exception(exc, {"source": "meta"})
        return {"status": "accepted"}


@router.post("/client")
async def client_receive(
    payload: dict,
    background_tasks: BackgroundTasks,
    client_id: str = Depends(require_client_token),
):
    try:
        background_tasks.add_task(handle_client_event, client_id, payload)
        return {"status": "accepted"}
    except Exception as exc:
        capture_exception(exc, {"source": "client", "client_id": client_id})
        return {"status": "accepted"}
