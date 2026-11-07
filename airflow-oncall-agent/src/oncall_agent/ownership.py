"""Incident ownership (FR-078).

Order:

1. **GitHub commit author** — of the suspected commit (from the analysis), otherwise the
   highest-scoring *possibly related* commit. Candidates: GitHub login, commit email,
   author name.
2. **DAG owner** — Airflow's ``owners`` (the placeholder owner ``airflow`` is ignored).
3. **Configured default assignee** — ``LINEAR_DEFAULT_ASSIGNEE``.
4. Otherwise the issue stays **unassigned** and the reason is recorded.

A candidate becomes a Linear user via ``ownership.user_map`` (config) first, then by
looking it up in Linear when it is an email address or a Linear user ID. Every step's
outcome is recorded in ``note`` so the audit trail shows why someone was (not) assigned.
A Linear lookup failure moves on to the next candidate; it never fails the incident.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .exceptions import LinearError
from .integrations.linear import LinearClient
from .models import Incident

log = logging.getLogger(__name__)

IGNORED_OWNERS = {"airflow", "", "none", "unknown"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


@dataclass
class Ownership:
    assignee_id: str | None
    source: str  # commit_author | dag_owner | default_assignee | none
    who: str | None
    notes: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


class OwnershipResolver:
    def __init__(self, linear: LinearClient, user_map: dict[str, str], default_assignee: str | None) -> None:
        self.linear = linear
        # Case-insensitive keys: GitHub logins and emails are case-insensitive.
        self.user_map = {k.lower(): v for k, v in user_map.items()}
        self.default_assignee = default_assignee

    def resolve(self, incident: Incident) -> Ownership:
        notes: list[str] = []
        summary: dict[str, Any] = incident.context_summary or {}

        commit = self._commit(incident, summary)
        if commit is None:
            notes.append("commit author: no suspected or possibly related commit")
        else:
            for candidate in (commit.get("author_login"), commit.get("author_email"), commit.get("author_name")):
                user_id = self._lookup(candidate, notes, "commit author")
                if user_id:
                    return Ownership(user_id, "commit_author", candidate, notes + [f"assigned to author of {commit['sha'][:7]} ({candidate})"])

        owners = [o for o in summary.get("dag_owners", []) if o and o.strip().lower() not in IGNORED_OWNERS]
        if not owners:
            notes.append("DAG owner: none recorded (or only the default 'airflow')")
        for owner in owners:
            user_id = self._lookup(owner, notes, "DAG owner")
            if user_id:
                return Ownership(user_id, "dag_owner", owner, notes + [f"assigned to DAG owner {owner}"])

        if self.default_assignee:
            user_id = self._lookup(self.default_assignee, notes, "default assignee")
            if user_id:
                return Ownership(user_id, "default_assignee", self.default_assignee, notes + ["assigned to the configured default assignee"])
        else:
            notes.append("default assignee: not configured")

        return Ownership(None, "none", None, notes + ["left unassigned: no owner could be resolved"])

    @staticmethod
    def _commit(incident: Incident, summary: dict[str, Any]) -> dict[str, Any] | None:
        commits = summary.get("commits", [])
        if incident.suspected_commit:
            for c in commits:
                if c.get("sha", "").startswith(incident.suspected_commit[:7]):
                    return c
        related = sorted((c for c in commits if c.get("possibly_related")), key=lambda c: -c.get("relation_score", 0))
        return related[0] if related else None

    def _lookup(self, candidate: str | None, notes: list[str], role: str) -> str | None:
        if not candidate:
            return None
        mapped = self.user_map.get(candidate.lower())
        target = mapped or candidate
        if not mapped and "@" not in target and not _UUID.match(target):
            notes.append(f"{role} {candidate}: not in ownership.user_map and not an email or Linear user ID")
            return None
        try:
            user = self.linear.find_user(target)
        except LinearError as exc:
            notes.append(f"{role} {candidate}: Linear lookup failed ({exc})")
            return None
        if user is None:
            notes.append(f"{role} {candidate}: no Linear user {target}")
            return None
        return user.id
