"""
eon.memory
============
Memoria avanzada: episódica, semántica, skills, patrones de fallo,
replay y aprendizaje de pesos de verificación.

El kernel aprende de cada ejecución:
- EpisodicMemory: persiste ejecuciones completas como episodios
- SemanticMemory: vector store con embeddings para búsqueda por similitud
- SkillLibrary: planes exitosos reutilizables
- FailurePatterns: categorización y estadísticas de fallos
- Replay: reproduce ejecuciones paso a paso desde EventStore
- VerificationLearning: ajuste online de pesos de verificación
"""

from __future__ import annotations

from .episodic import Episode, EpisodicMemoryStore
from .failure_patterns import FailureCategory, FailurePatterns, FailureRecord
from .replay import ExecutionReplayer, ReplayResult, ReplayStep
from .semantic import EmbeddingProvider, HashingEmbedder, InMemoryVectorStore, SemanticMemory
from .skills import Skill, SkillLibrary
from .verification_learning import VerificationOutcome, VerificationWeightLearner


def get_chroma_backend():
    """Devuelve ChromaVectorStore si chromadb está instalado, None si no."""
    try:
        import chromadb  # noqa: F401

        from .chroma_backend import ChromaVectorStore

        return ChromaVectorStore
    except ImportError:
        return None


__all__ = [
    "Episode",
    "EpisodicMemoryStore",
    "EmbeddingProvider",
    "InMemoryVectorStore",
    "SemanticMemory",
    "HashingEmbedder",
    "Skill",
    "SkillLibrary",
    "FailurePatterns",
    "FailureCategory",
    "FailureRecord",
    "ExecutionReplayer",
    "ReplayStep",
    "ReplayResult",
    "VerificationWeightLearner",
    "VerificationOutcome",
    "get_chroma_backend",
]
