import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from brain.dispatcher import handle_shopify_event
from observability import capture_exception
from security import verify_shopify_hmac

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks"])


@router.post("")
async def shopify_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256")
    topic = request.headers.get("X-Shopify-Topic", "")
    shop = request.headers.get("X-Shopify-Shop-Domain", "")

    if not verify_shopify_hmac(raw_body, hmac_header):
        raise HTTPException(status_code=401, detail="Invalid HMAC")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        capture_exception(exc, {"source": "shopify", "shop": shop})
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        if topic == "orders/create":
            background_tasks.add_task(handle_shopify_event, payload, topic, shop)
    except Exception as exc:
        capture_exception(exc, {"source": "shopify", "topic": topic, "shop": shop})

    return {"status": "accepted"}
