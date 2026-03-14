from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import DesktopAgent
from routes import desktop_agent as desktop_agent_routes
from security import require_client_token

router = APIRouter(prefix="/console", tags=["console"])


@router.get("/runners")
async def list_runners(
    client_id: str = Depends(require_client_token),
    session: AsyncSession = Depends(get_session),
):
    org_id = desktop_agent_routes._parse_uuid(client_id, field="client_id")
    rows = list(
        (
            await session.execute(
                select(DesktopAgent)
                .where(DesktopAgent.organization_id == org_id)
                .order_by(desc(DesktopAgent.updated_at))
            )
        )
        .scalars()
        .all()
    )
    online_ids = {str(x) for x in desktop_agent_routes._ws_manager._connections.keys()}
    return {
        "client_id": client_id,
        "total": len(rows),
        "online": len([x for x in rows if x.status == "online"]),
        "items": [
            {
                "agent_id": str(row.id),
                "agent_name": row.agent_name,
                "environment": row.environment,
                "status": row.status,
                "is_connected": str(row.id) in online_ids,
                "last_heartbeat_at": (
                    row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None
                ),
                "connected_at": row.connected_at.isoformat() if row.connected_at else None,
                "disconnected_at": (
                    row.disconnected_at.isoformat() if row.disconnected_at else None
                ),
            }
            for row in rows
        ],
    }

