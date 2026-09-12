"""Embedding provider interfaces."""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Minimal interface required by retrieval backends."""

    model_name: str
    dimension: int

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Embed source documents in input order."""

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        """Embed one retrieval query."""
