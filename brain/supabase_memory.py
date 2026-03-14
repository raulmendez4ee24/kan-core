import os
from typing import Any, Dict, List, Optional

from langchain_community.vectorstores import SupabaseVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from supabase.client import Client, create_client

from brain.memory import VectorMemory


class SupabaseMemory(VectorMemory):
    _instance: Optional["SupabaseMemory"] = None

    def __init__(
        self, url: str, key: str, embedding_model: str = "models/embedding-001"
    ):
        self.client: Client = create_client(url, key)
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model=embedding_model,
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=768,
        )
        self.vector_store = SupabaseVectorStore(
            client=self.client,
            embedding=self.embeddings,
            table_name="chat_history",
            query_name="match_chat_history",
        )

    @classmethod
    def get_instance(cls) -> "SupabaseMemory":
        if cls._instance is None:
            url = os.getenv("SUPABASE_URL")
            key = os.getenv("SUPABASE_KEY")
            if not url or not key:
                raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are not set")
            cls._instance = cls(url, key)
        return cls._instance

    async def search_context(
        self, query: str, *, top_k: int = 5, filters: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """Return a ranked list of relevant context chunks."""
        docs = self.vector_store.similarity_search(query, k=top_k, filter=filters)
        return [doc.page_content for doc in docs]

    async def store_interaction(
        self,
        *,
        client_id: str,
        session_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store an interaction for long-term memory retrieval."""
        metadata = metadata or {}
        metadata["client_id"] = client_id
        metadata["session_id"] = session_id
        self.vector_store.add_texts([content], metadatas=[metadata])
