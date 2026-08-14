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
from .semantic import EmbeddingProvider, InMemoryVectorStore, SemanticMemory, HashingEmbedder
from .skills import Skill, SkillLibrary
from .failure_patterns import FailurePatterns, FailureCategory, FailureRecord
from .replay import ExecutionReplayer, ReplayStep, ReplayResult
from .verification_learning import VerificationWeightLearner, VerificationOutcome


def get_chroma_backend():
    """Devuelve ChromaVectorStore si chromadb está instalado, None si no."""
    try:
        from .chroma_backend import ChromaVectorStore
        import chromadb  # noqa: F401
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
