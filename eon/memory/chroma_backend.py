"""
eon.memory.chroma_backend
==========================
Backend de ChromaDB con persistencia real para SemanticMemory.

Si ChromaDB no está instalado, SemanticMemory cae automáticamente al
HashingEmbedder + InMemoryVectorStore determinista (sin dependencias).

Este módulo proporciona un VectorStore compatible con la interfaz que
SemanticMemory espera (métodos ``add``, ``search``, ``count``, ``clear``,
``delete``), respaldado por ChromaDB con persistencia en disco.

Características:
- **Persistencia real**: los datos sobreviven a reinicios del proceso.
- **Embeddings nativos de ChromaDB**: usa el modelo all-MiniLM-L6-v2
  integrado en ChromaDB (vía ONNX) en lugar del HashingEmbedder.
  Esto produce embeddings semánticamente significativos.
- **Modo híbrido**: opcionalmente se puede pasar un EmbeddingProvider
  personalizado y los vectores se computan con él en lugar del modelo
  nativo de ChromaDB.
- **Fallback automático**: si chromadb no está instalado,
  ``SemanticMemory.create_backend()`` cae a InMemoryVectorStore.
- **Distancia configurable**: coseno (por defecto), L2, o IP.

Uso::

    from eon.memory.chroma_backend import ChromaVectorStore

    # Persistencia en disco
    store = ChromaVectorStore(
        collection_name="eon_memory",
        persist_path="/data/chroma",
    )
    # Los datos persisten entre reinicios.

    # Usar el embedding nativo de ChromaDB (recomendado)
    store.add("id-1", "Desplegar API en producción")

    # O usar un EmbeddingProvider personalizado
    from eon.memory.semantic import HashingEmbedder
    store = ChromaVectorStore(
        persist_path="/data/chroma",
        embedding_provider=HashingEmbedder(),
    )

Diseño:
- Import lazy: ``chromadb`` solo se importa cuando se instancia el store.
- Fallback automático en ``SemanticMemory.create_backend()``.
- Sin dependencias obligatorias: todo funciona con stdlib si ChromaDB no está.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from .semantic import InMemoryVectorStore, VectorEntry

logger = logging.getLogger("eon.memory.chroma_backend")


class ChromaVectorStore(InMemoryVectorStore):
    """VectorStore respaldado por ChromaDB con persistencia real en disco.

    Si ChromaDB no está instalado, lanza ``ImportError`` al instanciarse.
    ``SemanticMemory.create_backend()`` captura este error y cae al
    ``InMemoryVectorStore`` por defecto.

    Hereda de ``InMemoryVectorStore`` para mantener la misma interfaz
    (``add``, ``search``, ``count``, ``clear``), pero delega
    internamente en ChromaDB para persistencia y búsqueda.

    Args:
        collection_name: Nombre de la colección en ChromaDB.
        persist_path: Ruta del directorio de persistencia. Si es None,
            usa un cliente efímero en memoria (sin persistencia).
        embedding_provider: Proveedor de embeddings personalizado. Si es
            None, usa el modelo nativo de ChromaDB (all-MiniLM-L6-v2).
        distance_metric: Métrica de distancia: "cosine" (por defecto),
            "l2", o "ip" (inner product).

    Persistencia:
        Cuando ``persist_path`` se proporciona, ChromaDB usa
        ``PersistentClient`` que escribe automáticamente al disco.
        Los datos sobreviven a reinicios del proceso: basta con
        crear un nuevo ``ChromaVectorStore`` con el mismo ``persist_path``
        y ``collection_name``.
    """

    def __init__(
        self,
        collection_name: str = "eon_memory",
        persist_path: str | None = None,
        embedding_provider: Any = None,
        distance_metric: str = "cosine",
    ) -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError(
                "chromadb no está instalado. Instala con: pip install chromadb. "
                "El sistema caerá automáticamente al InMemoryVectorStore."
            ) from exc

        # No llamar a super().__init__() — no necesitamos el _entries
        # en memoria. ChromaDB es la fuente de verdad.
        self._entries: list[VectorEntry] = []  # solo para compat de interfaz
        self._embedder = embedding_provider
        self._collection_name = collection_name
        self._persist_path = persist_path
        self._distance_metric = distance_metric
        self._chromadb_module = chromadb

        # Crear cliente y colección de ChromaDB
        if persist_path:
            os.makedirs(persist_path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=persist_path)
        else:
            self._client = chromadb.Client()

        # Configurar función de embedding
        if embedding_provider is not None:
            # Usar el proveedor personalizado: ChromaDB recibirá
            # embeddings pre-computados en cada add/query.
            collection_kwargs: dict[str, Any] = {
                "name": collection_name,
                "metadata": {"hnsw:space": distance_metric},
            }
            self._collection = self._client.get_or_create_collection(
                **collection_kwargs,
            )
        else:
            # Usar el embedding nativo de ChromaDB (all-MiniLM-L6-v2)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": distance_metric},
            )

        logger.debug(
            "ChromaVectorStore inicializado: collection=%s, persist_path=%s, embedding=%s, distance=%s, count=%d",
            collection_name,
            persist_path or "(ephemeral)",
            "custom" if embedding_provider else "native",
            distance_metric,
            self._collection.count(),
        )

    def add(self, entry_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        """Añade o actualiza una entrada en ChromaDB.

        Si ``embedding_provider`` fue configurado, computa el embedding
        con él. Si no, deja que ChromaDB use su modelo nativo.
        """
        clean_metadata = self._sanitize_metadata(metadata or {})
        if not clean_metadata:
            # ChromaDB requires non-empty metadata dicts
            clean_metadata = {"_placeholder": True}

        if self._embedder is not None:
            embedding = self._embedder.embed(text)
            self._collection.upsert(
                ids=[entry_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[clean_metadata],
            )
        else:
            self._collection.upsert(
                ids=[entry_id],
                documents=[text],
                metadatas=[clean_metadata],
            )

    def search(
        self,
        query: str,
        limit: int = 5,
        filter_fn: Any = None,
    ) -> list[tuple[float, VectorEntry]]:
        """Busca entradas similares en ChromaDB.

        Returns:
            Lista de (score, VectorEntry) ordenada por relevancia
            descendente. El score se normaliza a [0, 1] donde 1 es
            máxima similitud.
        """
        if self._embedder is not None:
            query_embedding = self._embedder.embed(query)
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
            )
        else:
            results = self._collection.query(
                query_texts=[query],
                n_results=limit,
            )

        scores: list[tuple[float, VectorEntry]] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i, doc_id in enumerate(ids):
            raw_meta = metadatas[i] if i < len(metadatas) else {}
            # Strip internal placeholder metadata
            if isinstance(raw_meta, dict):
                clean_meta = {k: v for k, v in raw_meta.items() if k != "_placeholder"}
            else:
                clean_meta = {}
            entry = VectorEntry(
                id=doc_id,
                text=documents[i] if i < len(documents) else "",
                embedding=[],
                metadata=clean_meta,
            )
            # ChromaDB devuelve distancia (menor = más similar).
            # Convertir a score de similitud: 1 / (1 + distancia)
            dist = distances[i] if i < len(distances) else 1.0
            score = 1.0 / (1.0 + abs(dist))

            if filter_fn is None or filter_fn(entry):
                scores.append((score, entry))

        return scores

    def delete(self, ids: list[str] | None = None, where: dict[str, Any] | None = None) -> None:
        """Elimina entradas de ChromaDB por IDs o por filtro de metadatos.

        Args:
            ids: Lista de IDs a eliminar. Si es None, se usa ``where``.
            where: Filtro de metadatos (ej. {"type": "objective"}).
        """
        self._collection.delete(
            ids=ids,
            where=where,
        )

    @property
    def count(self) -> int:
        """Número de entradas en la colección."""
        return self._collection.count()

    def clear(self) -> None:
        """Elimina todas las entradas de la colección.

        Esto borra la colección y la recrea vacía. Útil para tests.
        """
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": self._distance_metric},
        )

    def close(self) -> None:
        """Cierra el cliente de ChromaDB.

        ChromaDB PersistentClient persiste automáticamente al disco
        en cada operación, por lo que no es necesario un flush explícito.
        Este método limpia referencias internas.
        """
        self._collection = None
        self._client = None

    def destroy(self) -> None:
        """Elimina completamente el directorio de persistencia.

        Peligroso: borra todos los datos. Principalmente para tests.
        """
        self.close()
        if self._persist_path and os.path.exists(self._persist_path):
            shutil.rmtree(self._persist_path, ignore_errors=True)

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        """Sanitiza metadatos para compatibilidad con ChromaDB.

        ChromaDB solo acepta valores str, int, float, bool en metadatos.
        Convierte otros tipos a string.
        """
        clean: dict[str, Any] = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)):
                clean[key] = value
            elif value is None:
                continue  # ChromaDB no acepta None
            else:
                clean[key] = str(value)
        return clean
