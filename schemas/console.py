from typing import Optional

from pydantic import BaseModel, Field


class ConsoleLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=6, max_length=200)
    organization_id: Optional[str] = None

