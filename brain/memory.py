from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VectorMemory(ABC):
    """Abstract interface for vector database integrations (Pinecone/Chroma/etc.)."""

    @abstractmethod
    async def search_context(
        self, query: str, *, top_k: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Return a ranked list of relevant context chunks."""
        raise NotImplementedError

    @abstractmethod
    async def store_interaction(
        self,
        *,
        client_id: str,
        user_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store an interaction for long-term memory retrieval."""
        raise NotImplementedError


class NoOpMemory(VectorMemory):
    """Safe in-process fallback memory implementation."""

    async def search_context(
        self, query: str, *, top_k: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        _ = (query, top_k, filters)
        return []

    async def store_interaction(
        self,
        *,
        client_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        _ = (client_id, content, metadata, kwargs)
        return None
