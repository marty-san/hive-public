"""Embedding service using OpenAI."""
from openai import AsyncOpenAI
from typing import List
import structlog
from app.config import settings

logger = structlog.get_logger()


class EmbeddingService:
    """Service for generating embeddings using OpenAI."""

    def __init__(self):
        """Initialize OpenAI client."""
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = "text-embedding-3-small"
        self.dimensions = 1536  # text-embedding-3-small produces 1536-dimensional vectors

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding vector
        """
        embeddings = await self.generate_embeddings([text])
        return embeddings[0] if embeddings else []

    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batch.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        try:
            logger.info(
                "generating_embeddings",
                model=self.model,
                text_count=len(texts),
                total_chars=sum(len(t) for t in texts)
            )

            response = await self.client.embeddings.create(
                model=self.model,
                input=texts
            )

            embeddings = [item.embedding for item in response.data]

            logger.info(
                "embeddings_generated",
                count=len(embeddings),
                dimensions=len(embeddings[0]) if embeddings else 0,
                total_tokens=response.usage.total_tokens
            )

            return embeddings

        except Exception as e:
            logger.error("embedding_generation_error", error=str(e))
            raise

    async def generate_query_embedding(self, query: str) -> List[float]:
        """
        Generate embedding for a query (search).

        Note: OpenAI's text-embedding-3-small doesn't distinguish between
        document and query embeddings, so this uses the same method.

        Args:
            query: Query text

        Returns:
            Embedding vector for search
        """
        if not query:
            return []

        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=[query]
            )

            embedding = response.data[0].embedding

            logger.info(
                "query_embedding_generated",
                dimensions=len(embedding),
                query_length=len(query)
            )

            return embedding

        except Exception as e:
            logger.error("query_embedding_error", error=str(e))
            raise

    @staticmethod
    def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Similarity score between -1 and 1 (higher is more similar)
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0

        # Dot product
        dot_product = sum(a * b for a, b in zip(vec1, vec2))

        # Magnitudes
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)


# Singleton instance
embedding_service = EmbeddingService()
