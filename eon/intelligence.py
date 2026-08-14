"""
eon.intelligence
================
Integration layer that wires Fase 7-9 modules (planning, verification,
memory) into the runtime, WITHOUT modifying the Coordinator or its
state machine.

This module provides:
1. ``IntelligenceConfig`` — config for optional intelligence modules.
2. ``IntelligenceHooks`` — hooks called by KernelRuntime at key points:
   - After plan creation: score + simulate
   - After verification rejection: AutoReplanner suggests
   - After execution completion: persist Episode, register skill,
     record failure patterns, update verification weights
3. ``EnhancedKernelRuntime`` — subclass of KernelRuntime with
   intelligence wired in.

Design principles:
- All intelligence is opt-in: if not configured, behavior is identical
  to the base KernelRuntime.
- No changes to Coordinator state machine or Port contracts.
- Metrics recorded via MetricsRecorder (zero dependencies).
- Memory stores are lazy-initialized (only when needed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .runtime import KernelRuntime, KernelRunResult
from .telemetry import MetricsRecorder


@dataclass
class IntelligenceConfig:
    """Configuration for intelligence modules.

    All fields default to disabled/opt-out. Setting any to True
    enables that feature.
    """
    # Planning
    enable_plan_scoring: bool = False
    enable_plan_simulation: bool = False
    enable_auto_replanning: bool = False

    # Memory
    enable_episodic_memory: bool = False
    enable_semantic_memory: bool = False
    enable_skill_library: bool = False
    enable_failure_patterns: bool = False
    enable_verification_learning: bool = False

    # Verification
    enable_composite_verifier: bool = False

    # Console
    enable_console: bool = False
    console_port: int = 8080
    console_host: str = "127.0.0.1"
    # Auth: Bearer token. Si None, no auth.
    console_auth_token: str | None = None
    # TLS: rutas a cert/key. Si None, HTTP plano.
    console_tls_certfile: str | None = None
    console_tls_keyfile: str | None = None
    console_tls_auto_generate: bool = False

    # Telemetry (always on if intelligence is enabled)
    enable_metrics: bool = True

    # ChromaDB
    semantic_backend: str = "memory"  # "memory" or "chroma"
    # ChromaDB persistence options (only used when semantic_backend="chroma")
    chroma_persist_path: str | None = None  # None = ephemeral, path = persistent
    chroma_collection_name: str = "eon_memory"
    chroma_distance_metric: str = "cosine"  # "cosine", "l2", "ip"

    # LLM (for judge/decomposer)
    llm_provider: Any = None  # An LLM instance, or None

    @property
    def any_enabled(self) -> bool:
        """True if any intelligence feature is enabled."""
        return any([
            self.enable_plan_scoring,
            self.enable_plan_simulation,
            self.enable_auto_replanning,
            self.enable_episodic_memory,
            self.enable_semantic_memory,
            self.enable_skill_library,
            self.enable_failure_patterns,
            self.enable_verification_learning,
            self.enable_composite_verifier,
            self.enable_console,
        ])


class IntelligenceHooks:
    """Hooks that integrate intelligence modules into the runtime lifecycle.

    Called by EnhancedKernelRuntime at key lifecycle points. Each hook
    is a no-op if the corresponding feature is disabled.
    """

    def __init__(
        self,
        config: IntelligenceConfig,
        metrics: MetricsRecorder,
    ) -> None:
        self._config = config
        self._metrics = metrics

        # Lazy-initialized components
        self._plan_scorer = None
        self._plan_simulator = None
        self._auto_replanner = None
        self._episodic_store = None
        self._semantic_memory = None
        self._skill_library = None
        self._failure_patterns = None
        self._weight_learner = None

    # ─── Lazy initialization ───────────────────────────────────

    def _ensure_plan_scorer(self):
        if self._plan_scorer is None:
            from .planning import PlanScorer
            self._plan_scorer = PlanScorer()
        return self._plan_scorer

    def _ensure_plan_simulator(self):
        if self._plan_simulator is None:
            from .planning import PlanSimulator
            self._plan_simulator = PlanSimulator()
        return self._plan_simulator

    def _ensure_auto_replanner(self):
        if self._auto_replanner is None:
            from .planning import AutoReplanner
            skills = self._ensure_skill_library() if self._config.enable_skill_library else None
            self._auto_replanner = AutoReplanner(skill_library=skills)
        return self._auto_replanner

    def _ensure_episodic_store(self):
        if self._episodic_store is None:
            from .memory import EpisodicMemoryStore
            self._episodic_store = EpisodicMemoryStore()
        return self._episodic_store

    def _ensure_semantic_memory(self):
        if self._semantic_memory is None:
            from .memory import SemanticMemory
            store = SemanticMemory.create_backend(
                backend=self._config.semantic_backend,
                collection_name=self._config.chroma_collection_name,
                persist_path=self._config.chroma_persist_path,
                distance_metric=self._config.chroma_distance_metric,
            )
            self._semantic_memory = SemanticMemory(store=store)
        return self._semantic_memory

    def _ensure_skill_library(self):
        if self._skill_library is None:
            from .memory import SkillLibrary
            self._skill_library = SkillLibrary()
        return self._skill_library

    def _ensure_failure_patterns(self):
        if self._failure_patterns is None:
            from .memory import FailurePatterns
            self._failure_patterns = FailurePatterns()
        return self._failure_patterns

    def _ensure_weight_learner(self):
        if self._weight_learner is None:
            from .memory import VerificationWeightLearner
            self._weight_learner = VerificationWeightLearner()
        return self._weight_learner

    # ─── Hooks ────────────────────────────────────────────────

    def on_plan_created(
        self,
        plan_id: str,
        tasks: list[dict[str, Any]],
        success_criteria: str = "",
        available_capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Called after a plan is created (before scheduling).

        Scores and simulates the plan. Records metrics.

        Returns:
            Dict with ``score``, ``simulation``, and ``should_proceed``.
        """
        result: dict[str, Any] = {
            "score": None,
            "simulation": None,
            "should_proceed": True,
        }

        plan_dict = {"id": plan_id, "tasks": tasks}

        if self._config.enable_plan_scoring:
            scorer = self._ensure_plan_scorer()
            score = scorer.score(plan_dict, success_criteria)
            result["score"] = score
            self._metrics.gauge("plan.score", score.total_score)
            self._metrics.gauge("plan.score.efficiency", score.efficiency)
            self._metrics.gauge("plan.score.robustness", score.robustness)
            self._metrics.gauge("plan.score.coverage", score.coverage)
            self._metrics.gauge("plan.score.simplicity", score.simplicity)

        if self._config.enable_plan_simulation:
            simulator = self._ensure_plan_simulator()
            sim = simulator.simulate(plan_dict, available_capabilities)
            result["simulation"] = sim
            self._metrics.gauge("plan.simulation.confidence", sim.predicted_confidence)
            self._metrics.gauge("plan.simulation.cost", sim.predicted_cost_usd)
            self._metrics.gauge("plan.simulation.duration", sim.predicted_duration_seconds)
            if not sim.predicted_success:
                self._metrics.increment("plan.simulation.predicted_failure")

        return result

    def on_verification_rejected(
        self,
        execution_id: str,
        failure_reason: str,
        attempt_number: int,
        plan_id: str = "",
        previous_errors: list[str] | None = None,
        available_capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Called after the verifier rejects a result.

        Uses AutoReplanner to suggest a replan strategy.

        Returns:
            Replan suggestion dict, or None if auto-replanning is disabled.
        """
        if not self._config.enable_auto_replanning:
            return None

        self._metrics.increment("replan.triggered")

        replanner = self._ensure_auto_replanner()
        from .planning import ReplanContext

        context = ReplanContext(
            execution_id=execution_id,
            failure_reason=failure_reason,
            original_plan_id=plan_id,
            attempt_number=attempt_number,
            previous_errors=previous_errors or [],
            available_capabilities=available_capabilities or [],
        )

        suggestion = replanner.replan(context)
        if suggestion and suggestion.get("should_replan"):
            self._metrics.increment("replan.suggested")
        else:
            self._metrics.increment("replan.aborted")

        return suggestion

    def on_execution_completed(
        self,
        execution_id: str,
        objective_description: str,
        success_criteria: str,
        result_cumple: bool,
        result_confidence: float,
        plan_summary: str = "",
        duration_seconds: float = 0.0,
        tasks_total: int = 0,
        tasks_completed: int = 0,
        tasks_failed: int = 0,
        failed_task_ids: list[str] | None = None,
        replans: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Called after an execution completes (success or failure).

        Persists episode, registers skill, records failure patterns,
        updates verification weights.
        """
        self._metrics.increment(
            "executions.completed" if result_cumple else "executions.failed"
        )
        self._metrics.gauge("execution.confidence", result_confidence)
        self._metrics.gauge("execution.duration", duration_seconds)
        self._metrics.gauge("execution.tasks_total", tasks_total)
        self._metrics.gauge("execution.tasks_completed", tasks_completed)

        # Episodic memory
        if self._config.enable_episodic_memory:
            from .memory import Episode
            import uuid as _uuid

            store = self._ensure_episodic_store()
            episode = Episode(
                id=str(_uuid.uuid4()),
                execution_id=execution_id,
                objective_description=objective_description,
                success_criteria=success_criteria,
                plan_summary=plan_summary,
                result_cumple=result_cumple,
                result_confidence=result_confidence,
                duration_seconds=duration_seconds,
                failed_tasks=failed_task_ids or [],
                replans=replans,
                tasks_total=tasks_total,
                tasks_completed=tasks_completed,
                tasks_failed=tasks_failed,
                metadata=metadata or {},
            )
            store.save(episode)
            self._metrics.increment("memory.episodic.saved")

        # Semantic memory
        if self._config.enable_semantic_memory:
            sm = self._ensure_semantic_memory()
            sm.remember(
                entry_id=execution_id,
                text=f"{objective_description} {success_criteria}",
                metadata={
                    "success": result_cumple,
                    "confidence": result_confidence,
                    "execution_id": execution_id,
                },
            )
            self._metrics.increment("memory.semantic.saved")

        # Skill library (only on success)
        if self._config.enable_skill_library and result_cumple:
            from .memory import Skill
            import uuid as _uuid

            lib = self._ensure_skill_library()
            skill = Skill(
                id=str(_uuid.uuid4()),
                name=objective_description[:100],
                objective_pattern=success_criteria,
                plan_summary=plan_summary,
                success_count=1,
            )
            lib.register(skill)
            self._metrics.increment("memory.skill.registered")

        # Failure patterns (only on failure)
        if self._config.enable_failure_patterns and not result_cumple:
            from .memory import FailureRecord, FailureCategory
            import uuid as _uuid

            patterns = self._ensure_failure_patterns()
            for task_id in (failed_task_ids or []):
                record = FailureRecord(
                    id=str(_uuid.uuid4()),
                    execution_id=execution_id,
                    task_id=task_id,
                    category=FailureCategory.UNKNOWN.value,
                    error_message=f"Task {task_id} failed during execution",
                )
                patterns.record(record)
            self._metrics.increment("memory.failure.recorded")

        # Verification learning
        if self._config.enable_verification_learning:
            learner = self._ensure_weight_learner()
            learner.record_outcome(
                execution_id=execution_id,
                layer_name="composite",
                predicted_confidence=result_confidence,
                actual_correct=result_cumple,
            )
            self._metrics.increment("memory.verification.learned")

    def on_verification_result(self, confidence: float, dictamen: str) -> None:
        """Record verification metrics after each verification call."""
        self._metrics.gauge("verification.confidence", confidence)
        self._metrics.increment(f"verification.{dictamen}")

    def close(self) -> None:
        """Clean up resources."""
        # Close any stores that have close() methods
        for store in [
            self._episodic_store,
            self._semantic_memory,
            self._skill_library,
            self._failure_patterns,
            self._weight_learner,
        ]:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class EnhancedKernelRuntime(KernelRuntime):
    """KernelRuntime with intelligence modules wired in.

    All intelligence features are opt-in via ``intelligence_config``.
    If not provided, behavior is identical to ``KernelRuntime``.

    The enhancement is non-invasive:
    - ``run()`` calls ``on_execution_completed`` after the run.
    - The verifier is wrapped with ``CompositeVerifierAdapter`` if enabled.
    - Plan scoring/simulation hooks are available via ``score_plan()``.
    - Auto-replanning is available via ``suggest_replan()``.
    - Console server starts if enabled.
    """

    def __init__(
        self,
        *args: Any,
        intelligence_config: IntelligenceConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)

        self._intel_config = intelligence_config or IntelligenceConfig()
        self._metrics = MetricsRecorder()

        self._hooks = IntelligenceHooks(
            config=self._intel_config,
            metrics=self._metrics,
        )

        # Wire CompositeVerifier if enabled
        if self._intel_config.enable_composite_verifier:
            self._wire_composite_verifier()

        # Start console server if enabled
        self._console_server = None
        if self._intel_config.enable_console:
            self._wire_console()

    def _wire_composite_verifier(self) -> None:
        """Replace the basic verifier with CompositeVerifierAdapter."""
        from .verification import CompositeVerifier, CompositeVerifierAdapter

        composite = CompositeVerifier()
        adapter = CompositeVerifierAdapter(composite=composite)
        self._verifier = adapter
        # Update coordinator's verifier reference
        self.coordinator._verifier = adapter

    def _wire_console(self) -> None:
        """Start the console HTTP/HTTPS server."""
        from .console import ConsoleServer

        self._console_server = ConsoleServer(
            runtime=self,
            port=self._intel_config.console_port,
            host=self._intel_config.console_host,
            metrics=self._metrics,
            auth_token=self._intel_config.console_auth_token,
            tls_certfile=self._intel_config.console_tls_certfile,
            tls_keyfile=self._intel_config.console_tls_keyfile,
            tls_auto_generate=self._intel_config.console_tls_auto_generate,
        )
        self._console_server.start()

    @property
    def metrics(self) -> MetricsRecorder:
        """Access the metrics recorder."""
        return self._metrics

    @property
    def hooks(self) -> IntelligenceHooks:
        """Access the intelligence hooks."""
        return self._hooks

    def score_plan(
        self,
        plan_id: str,
        tasks: list[dict[str, Any]],
        success_criteria: str = "",
        available_capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Score and simulate a plan. Convenience wrapper."""
        return self._hooks.on_plan_created(
            plan_id=plan_id,
            tasks=tasks,
            success_criteria=success_criteria,
            available_capabilities=available_capabilities,
        )

    def suggest_replan(
        self,
        execution_id: str,
        failure_reason: str,
        attempt_number: int,
        plan_id: str = "",
        previous_errors: list[str] | None = None,
        available_capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Get a replan suggestion from AutoReplanner."""
        return self._hooks.on_verification_rejected(
            execution_id=execution_id,
            failure_reason=failure_reason,
            attempt_number=attempt_number,
            plan_id=plan_id,
            previous_errors=previous_errors,
            available_capabilities=available_capabilities,
        )

    def run(
        self,
        descripcion: str,
        criterio_de_exito: str,
        configuracion: dict | None = None,
        execution_id: str | None = None,
    ) -> KernelRunResult:
        """Run the kernel and record intelligence metrics after completion."""
        start_time = time.monotonic()

        result = super().run(
            descripcion, criterio_de_exito, configuracion, execution_id
        )

        duration = time.monotonic() - start_time

        # Gather execution data for intelligence hooks
        execution = self._coordinator_store.get(result.execution_id)
        objective_id = getattr(execution, "objective_id", "") if execution else ""
        plan_id = getattr(execution, "plan_id", "") if execution else ""

        # Try to get objective info
        try:
            objective = self._objective_manager.obtener(objective_id) if objective_id else None
            result_cumple = objective.estado == "completado" if objective else True
        except Exception:
            result_cumple = True

        # Try to get scheduler stats
        tasks_total = 0
        tasks_completed = 0
        tasks_failed = 0
        try:
            if plan_id:
                run = self._scheduler_store.obtener(plan_id)
                tasks_total = len(run.tasks)
                tasks_completed = sum(
                    1 for r in run.tasks.values()
                    if r.estado.value == "completed"
                )
                tasks_failed = sum(
                    1 for r in run.tasks.values()
                    if r.estado.value == "failed"
                )
        except Exception:
            pass

        # Record verification result if available
        verifier = getattr(self, "_verifier", None)
        if verifier is not None:
            last_confidence = getattr(verifier, "last_confidence", 0.0)
            last_dictamen = getattr(verifier, "last_dictamen", "aprobado")
            if last_confidence > 0 or last_dictamen:
                self._hooks.on_verification_result(last_confidence, last_dictamen)

        # Call execution completed hook
        self._hooks.on_execution_completed(
            execution_id=result.execution_id,
            objective_description=descripcion,
            success_criteria=criterio_de_exito,
            result_cumple=result_cumple,
            result_confidence=getattr(verifier, "last_confidence", 1.0 if result_cumple else 0.0),
            plan_summary=f"plan={plan_id}",
            duration_seconds=duration,
            tasks_total=tasks_total,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
        )

        return result

    def close(self) -> None:
        """Close the runtime and all intelligence resources."""
        self._hooks.close()
        if self._console_server is not None:
            self._console_server.stop()
        super().close()
