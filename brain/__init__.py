from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brain.core import CognitiveEngine

__all__ = ["CognitiveEngine"]


def __getattr__(name: str):
    if name == "CognitiveEngine":
        from brain.core import CognitiveEngine

        return CognitiveEngine
    raise AttributeError(f"module 'brain' has no attribute {name!r}")
