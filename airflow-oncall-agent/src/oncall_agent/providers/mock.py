"""Offline mock AI provider (SRS Part 14, NFR-030/031). Never allowed in production.

Answers like a well-behaved model, deterministically, from the prompt data, so local
runs and the six simulation scenarios work without an API key. Behaviour for the sample
scenarios:

=====================================  ===============  ==========  ======================
error                                  classification   confidence  proposed action
=====================================  ===============  ==========  ======================
ReadTimeout / connection / 5xx         retriable        0.85        retry_task
LockNotAvailable / deadlock            retriable        0.80        retry_task
KeyError etc. + possibly related diff  non_retriable    0.90        manual_investigation
SyntaxError (import error)             non_retriable    0.95        manual_investigation
return code -9 / killed                uncertain        0.40        manual_investigation
DAG run failed without failed task     retriable        0.75        rerun_dag (restricted)
anything else                          uncertain        0.30        manual_investigation
=====================================  ===============  ==========  ======================

Tests can override the raw output (``responses``) or make it fail (``unavailable``,
``fail_next``) to exercise validation and fallback.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from ..exceptions import AIProviderError
from .base import AIProvider, AnalysisPrompt


class MockAIProvider(AIProvider):
    name = "mock"

    def __init__(self) -> None:
        self.unavailable = False
        self.responses: list[str] = []  # raw outputs returned in order before the rules
        self._failures: list[Exception] = []
        self.prompts: list[AnalysisPrompt] = []
        self._lock = threading.Lock()

    @property
    def model(self) -> str | None:
        return "mock-analyst-1"

    def fail_next(self, exc: Exception, times: int = 1) -> None:
        self._failures.extend([exc] * times)

    def check_connection(self) -> dict[str, Any]:
        if self.unavailable:
            raise AIProviderError("mock AI provider unavailable")
        return {"provider": "mock", "model": self.model}

    def complete(self, prompt: AnalysisPrompt) -> str:
        with self._lock:
            self.prompts.append(prompt)
            if self.unavailable:
                raise AIProviderError("mock AI provider unavailable")
            if self._failures:
                raise self._failures.pop(0)
            if self.responses:
                return self.responses.pop(0)
        return json.dumps(self._analyze(prompt.data))

    @staticmethod
    def _analyze(data: dict[str, Any]) -> dict[str, Any]:
        error = data.get("error", {}).get("original_line") or ""
        failure_type = data.get("failure", {}).get("type")
        related = [c for c in data.get("commits", []) if c.get("possibly_related")]
        missing = data.get("missing_evidence", [])
        history = data.get("history", {}).get("summary")
        evidence = [f"error line: {error}"] if error else []
        if history:
            evidence.append(history)

        def result(classification: str, confidence: float, action: str, root: str, fix: str, commit: str | None = None) -> dict[str, Any]:
            ev = evidence + ([f"commit {commit[:7]} changed code referenced by the error"] if commit else [])
            if missing:
                ev.append(f"not available: {', '.join(missing)}")
            return {
                "classification": classification, "confidence": confidence, "root_cause": root,
                "evidence": ev or ["no task log available"], "suggested_fix": fix, "proposed_action": action,
                "suspected_commit": commit,
                "explanation": f"Mock analysis: {root} Recommended action {action} with confidence {confidence}.",
            }  # fmt: skip

        if failure_type == "dag_run_failure":
            return result("retriable", 0.75, "rerun_dag", "The DAG run timed out without a task error; likely scheduler or capacity pressure.",
                          "Re-run the failed run after checking scheduler health.")  # fmt: skip
        if re.search(r"ReadTimeout|ConnectTimeout|ConnectionError|Read timed out|\b50[234]\b", error):
            return result("retriable", 0.85, "retry_task", "Transient timeout calling an upstream API; previous runs succeeded and no related code changed.",
                          "Retry the task. Consider adding retries with exponential backoff to the operator.")  # fmt: skip
        if re.search(r"LockNotAvailable|deadlock|lock timeout", error, re.IGNORECASE):
            return result("retriable", 0.8, "retry_task", "Database lock timeout caused by a concurrent job.",
                          "Retry the task once the competing job has finished.")  # fmt: skip
        if re.search(r"SyntaxError|IndentationError", error):
            return result("non_retriable", 0.95, "manual_investigation", "The DAG file has a syntax error and cannot be imported.",
                          "Fix the syntax error in the DAG file and redeploy.")  # fmt: skip
        if re.search(r"KeyError|AttributeError|TypeError|NameError", error):
            commit = related[0]["sha"] if related else None
            return result("non_retriable", 0.9 if commit else 0.7, "manual_investigation",
                          "The task code references a field that the input data does not contain" + (" after a recent code change." if commit else "."),
                          "Align the code with the actual input schema (or revert the change) and redeploy.", commit)  # fmt: skip
        if re.search(r"return code -9|Killed|MemoryError", error):
            return result("uncertain", 0.4, "manual_investigation", "The process was killed, possibly out of memory; the logs do not show why.",
                          "Check worker memory usage and input data volume.")  # fmt: skip
        return result("uncertain", 0.3, "manual_investigation", "The cause could not be determined from the available evidence.",
                      "Investigate the task log manually.")  # fmt: skip
