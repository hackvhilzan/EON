"""
eon.memory.semantic
=====================
Memoria semántica: vector store con embeddings para búsqueda por similitud.

Backend por defecto: InMemoryVectorStore (determinista, sin dependencias).
Backend opcional: ChromaDB (si está instalado).

EmbeddingProvider: interfaz para generar embeddings.
HashingEmbedder: implementación determinista sin dependencias externas.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("eon.memory.semantic")


class EmbeddingProvider(Protocol):
    """Protocol para un proveedor de embeddings."""

    def embed(self, text: str) -> list[float]:
        """Genera un embedding vectorial para el texto."""
        ...


class HashingEmbedder:
    """Embedder determinista basado en hashing.

    No requiere dependencias externas. Genera vectores de dimensión
    fija usando hashing de palabras. No es tan bueno como un modelo
    real de embeddings, pero es determinista y suficiente para tests.
    """

    def __init__(self, dim: int = 128) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        words = text.lower().split()
        vec = [0.0] * self._dim
        for word in words:
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            idx = h % self._dim
            sign = 1.0 if (h // self._dim) % 2 == 0 else -1.0
            vec[idx] += sign
        # Normalizar
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


@dataclass
class VectorEntry:
    """Entrada en el vector store."""

    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemoryVectorStore:
    """Vector store en memoria con similitud coseno.

    Determinista y sin dependencias externas.
    """

    def __init__(self, embedder: EmbeddingProvider | None = None) -> None:
        self._embedder = embedder or HashingEmbedder()
        self._entries: list[VectorEntry] = []

    def add(self, entry_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        embedding = self._embedder.embed(text)
        self._entries.append(
            VectorEntry(
                id=entry_id,
                text=text,
                embedding=embedding,
                metadata=metadata or {},
            )
        )

    def search(
        self,
        query: str,
        limit: int = 5,
        filter_fn: Any = None,
    ) -> list[tuple[float, VectorEntry]]:
        """Busca entradas por similitud coseno.

        Returns:
            Lista de (score, entry) ordenada por score descendente.
        """
        query_vec = self._embedder.embed(query)
        scored: list[tuple[float, VectorEntry]] = []

        for entry in self._entries:
            if filter_fn and not filter_fn(entry):
                continue
            score = self._cosine_similarity(query_vec, entry.embedding)
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    @property
    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


class SemanticMemory:
    """Memoria semántica con vector store.

    Almacena embeddings de descripciones de objetivos, criterios de
    éxito y resultados. Permite buscar por similitud semántica.

    Usa InMemoryVectorStore por defecto. Si ChromaDB está instalado
    y se pasa como backend, lo usa automáticamente.
    """

    def __init__(self, store: InMemoryVectorStore | None = None) -> None:
        self._store = store or InMemoryVectorStore()

    @staticmethod
    def create_backend(
        backend: str = "memory",
        collection_name: str = "eon_memory",
        persist_path: str | None = None,
        embedding_provider: Any = None,
        distance_metric: str = "cosine",
    ) -> InMemoryVectorStore:
        """Crea el backend de vector store apropiado.

        Args:
            backend: "memory" (por defecto, sin dependencias) o "chroma"
                (requiere chromadb instalado, con persistencia real).
            collection_name: Nombre de la colección (solo ChromaDB).
            persist_path: Ruta de persistencia en disco (solo ChromaDB).
                Si es None, usa un cliente efímero en memoria.
            embedding_provider: Proveedor de embeddings personalizado.
                Si es None, ChromaDB usa su modelo nativo (all-MiniLM-L6-v2).
            distance_metric: Métrica de distancia: "cosine", "l2", "ip".

        Returns:
            Un VectorStore. Si backend="chroma" pero chromadb no está
            instalado, cae automáticamente a InMemoryVectorStore.
        """
        if backend == "chroma":
            try:
                from .chroma_backend import ChromaVectorStore

                return ChromaVectorStore(
                    collection_name=collection_name,
                    persist_path=persist_path,
                    embedding_provider=embedding_provider,
                    distance_metric=distance_metric,
                )
            except ImportError:
                pass  # Fallback silencioso a InMemoryVectorStore
        return InMemoryVectorStore()

    def remember(
        self,
        entry_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Almacena un recuerdo en la memoria semántica."""
        self._store.add(entry_id, text, metadata)

    def recall(
        self,
        query: str,
        limit: int = 5,
        filter_fn: Any = None,
    ) -> list[tuple[float, str, dict[str, Any]]]:
        """Recuerda entradas similares a la consulta.

        Returns:
            Lista de (score, text, metadata) ordenada por relevancia.
        """
        results = self._store.search(query, limit, filter_fn)
        return [(score, entry.text, entry.metadata) for score, entry in results]

    @property
    def size(self) -> int:
        return self._store.count
