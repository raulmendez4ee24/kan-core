from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PackRead(BaseModel):
    id: str
    title: str
    description: str
    industry: Optional[str] = None
    version: str = "1.0"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PacksListResponse(BaseModel):
    items: List[PackRead] = Field(default_factory=list)


class PackInstallResponse(BaseModel):
    pack_id: str
    installed: bool
    created_task_ids: List[str] = Field(default_factory=list)
    created_objective_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

