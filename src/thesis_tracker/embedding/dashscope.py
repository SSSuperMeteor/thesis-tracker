"""Thin DashScope ``qwen3-vl-embedding`` text client."""

from __future__ import annotations

import os
import time
from http import HTTPStatus
from typing import Any

from dashscope import MultiModalEmbedding

MODEL_NAME = "qwen3-vl-embedding"
EMBEDDING_DIMENSION = 1024
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


class EmbeddingAPIError(RuntimeError):
    """Raised when DashScope cannot return valid embeddings."""


class DashScopeEmbeddingClient:
    """Native DashScope multimodal client using text-only inputs."""

    model_name = MODEL_NAME
    dimension = EMBEDDING_DIMENSION

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise EmbeddingAPIError(
                "DASHSCOPE_API_KEY is not set"
            )
        if max_retries < 1:
            raise ValueError(
                "max_retries must be at least one"
            )

        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._api_key = api_key

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        """Embed one API batch of source texts."""
        return self._embed(texts)

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        """Embed one query using the same model and dimension."""
        embeddings = self._embed([text])
        return embeddings[0]

    def _embed(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        if not texts or any(not text for text in texts):
            raise ValueError(
                "embedding input must contain non-empty text"
            )

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = MultiModalEmbedding.call(
                    model=self.model_name,
                    input=[{"text": text} for text in texts],
                    dimension=self.dimension,
                    api_key=self._api_key,
                    base_address=DEFAULT_BASE_URL,
                    request_timeout=self.timeout,
                )
                if response.status_code != HTTPStatus.OK:
                    code = getattr(response, "code", None)
                    raise EmbeddingAPIError(
                        "DashScope returned an API error"
                        + (f" ({code})" if code else "")
                    )
                output: dict[str, Any] = response.output
                ordered = sorted(
                    output["embeddings"],
                    key=lambda item: int(item["index"]),
                )
                embeddings = [
                    list(item["embedding"])
                    for item in ordered
                ]
                self._validate(
                    embeddings,
                    expected_count=len(texts),
                )
                return embeddings
            except Exception as error:  # noqa: BLE001
                last_error = error
                if attempt + 1 < self.max_retries:
                    time.sleep(
                        self.retry_delay
                        * (2**attempt)
                    )

        error_type = (
            type(last_error).__name__
            if last_error is not None
            else "UnknownError"
        )
        raise EmbeddingAPIError(
            "DashScope embedding request failed after "
            f"{self.max_retries} attempts "
            f"({error_type})"
        ) from last_error

    def _validate(
        self,
        embeddings: list[list[float]],
        *,
        expected_count: int,
    ) -> None:
        if len(embeddings) != expected_count:
            raise EmbeddingAPIError(
                "DashScope returned an unexpected number "
                "of embeddings"
            )
        if any(
            len(embedding) != self.dimension
            for embedding in embeddings
        ):
            raise EmbeddingAPIError(
                "DashScope returned an unexpected "
                "embedding dimension"
            )
