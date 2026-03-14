from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import OrgMembership, Permission, Role, RolePermission


async def resolve_role_and_permissions(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
) -> Tuple[str, List[str]]:
    """Resolve role + permission keys from DB.

    This is "gradual RBAC": if no membership/role exists, callers should fallback to
    conservative defaults (ORG_USER).
    """
    membership_stmt = select(OrgMembership).where(
        OrgMembership.organization_id == organization_id,
        OrgMembership.user_id == user_id,
    )
    membership = (await session.execute(membership_stmt)).scalars().first()
    if not membership:
        return "ORG_USER", []
    if not membership.role_id:
        # Owner fallback: treat as ORG_ADMIN even without explicit DB role assignment.
        return ("ORG_ADMIN" if bool(membership.is_owner) else "ORG_USER"), []

    role_stmt = select(Role).where(Role.id == membership.role_id).limit(1)
    role = (await session.execute(role_stmt)).scalars().first()
    role_name = str(role.name) if role and role.name else "ORG_USER"

    perm_stmt = (
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == membership.role_id)
    )
    rows = list((await session.execute(perm_stmt)).scalars().all())
    permissions: List[str] = []
    for item in rows:
        token = str(item or "").strip()
        if token and token not in permissions:
            permissions.append(token)
    return role_name, permissions


def require_org_access(
    *,
    principal: dict,
    organization_id: UUID,
    allow_platform_roles: Optional[List[str]] = None,
) -> None:
    """Raise ValueError when principal cannot act on this organization."""
    allow_platform_roles = allow_platform_roles or ["SUPER_ADMIN", "PLATFORM_ADMIN"]
    role = str(principal.get("role") or "")
    if role in allow_platform_roles:
        return
    principal_org = str(principal.get("org") or "")
    if not principal_org:
        raise ValueError("Token missing organization scope")
    if principal_org != str(organization_id):
        raise ValueError("Organization mismatch")
