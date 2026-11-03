"""The agent loop: send the conversation to the model, run the tools it asks for, repeat.

Rules enforced here (not left to Claude):
- At most MAX_AGENT_STEPS model calls per prompt.
- Only tools in the registry can run; arguments are validated with Pydantic first.
- Write tools are never executed here. They become pending operations for the user.
- Tool results are size-limited before they go back to Claude.

History is append-only: earlier messages are never edited, which keeps Claude's
thinking blocks valid across turns.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.agent.claude_client import MessageCreator
from app.agent.prompts import build_system_prompt
from app.agent.tools import TOOLS_BY_NAME, claude_tool_definitions
from app.approvals import store_proposal
from app.errors import AppError
from app.services.common import ServiceContext
from app.sessions import PendingOperation, Session

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRecord:
    name: str
    kind: str  # read | write | unknown
    status: str  # ok | error | proposed
    message: str = ""

    def public(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind, "status": self.status, "message": self.message}


@dataclass
class AgentResult:
    reply: str
    steps: int
    stop_reason: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    proposed: list[PendingOperation] = field(default_factory=list)


def _block_attr(block: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK content block object or a plain dict."""
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def limit_tool_result(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n...[truncated: {omitted} more characters omitted. Narrow the request to see more.]"


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err.get("loc", ())) or "input"
        parts.append(f"{location}: {err.get('msg', 'invalid')}")
    return "Invalid arguments: " + "; ".join(parts)


class Agent:
    def __init__(self, model: MessageCreator, ctx: ServiceContext):
        self._model = model
        self._ctx = ctx
        self._settings = ctx.settings
        self._system = build_system_prompt(ctx.settings)
        self._tools = claude_tool_definitions()

    # ----- public -----

    def run(self, session: Session, prompt: str) -> AgentResult:
        """Answer one user prompt. On any error the session is restored and the error raised."""
        history_length = len(session.messages)
        saved_carryover = list(session.carryover_blocks)
        saved_notes = list(session.pending_notes)
        proposed: list[PendingOperation] = []  # shown to the user (may include reused operations)
        created: list[PendingOperation] = []  # created during this prompt

        session.messages.append({"role": "user", "content": self._user_content(session, prompt)})
        try:
            return self._loop(session, proposed, created)
        except Exception:
            # Roll back to the last state Claude has already seen, so the history stays valid.
            del session.messages[history_length:]
            session.carryover_blocks = saved_carryover
            session.pending_notes = saved_notes
            for op in created:
                op.status = "cancelled"
            raise

    # ----- internals -----

    def _user_content(self, session: Session, prompt: str) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = list(session.carryover_blocks)  # tool_results must come first
        session.carryover_blocks = []
        now = self._ctx.now().astimezone(self._settings.tz)
        text = f"[Current time: {now:%A %Y-%m-%d %H:%M} {self._settings.user_timezone}]\n"
        if session.pending_notes:
            text += "[Approval update]\n" + "\n".join(f"- {note}" for note in session.pending_notes) + "\n"
            session.pending_notes = []
        content.append({"type": "text", "text": text + "\n" + prompt})
        return content

    def _loop(
        self, session: Session, proposed: list[PendingOperation], created: list[PendingOperation]
    ) -> AgentResult:
        records: list[ToolCallRecord] = []
        max_steps = self._settings.max_agent_steps

        for step in range(1, max_steps + 1):
            response = self._model.create(system=self._system, tools=self._tools, messages=session.messages)
            content = list(_block_attr(response, "content", []) or [])
            stop_reason = str(_block_attr(response, "stop_reason", "") or "")
            session.messages.append({"role": "assistant", "content": content})

            if stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in content if _block_attr(b, "type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                if tool_uses:
                    # Cut off mid tool call (e.g. max_tokens): answer those calls in the next user message.
                    session.carryover_blocks = [
                        self._error_result(b, "Not executed: the response was cut off.") for b in tool_uses
                    ]
                return AgentResult(
                    reply=self._final_text(content, stop_reason),
                    steps=step, stop_reason=stop_reason, tool_calls=records, proposed=proposed,
                )

            results = [self._run_tool(session, block, records, proposed, created) for block in tool_uses]
            if step == max_steps:
                # Out of steps: keep the results for the next prompt instead of calling Claude again.
                session.carryover_blocks = results
                break
            session.messages.append({"role": "user", "content": results})

        return AgentResult(
            reply=(
                f"I stopped after {max_steps} steps without finishing. Here is what happened so far; "
                "you can ask me to continue or narrow the request."
            ),
            steps=max_steps, stop_reason="max_steps", tool_calls=records, proposed=proposed,
        )

    def _final_text(self, content: list[Any], stop_reason: str) -> str:
        text = "\n".join(
            _block_attr(b, "text", "") for b in content if _block_attr(b, "type") == "text"
        ).strip()
        if stop_reason == "refusal":
            return text or "The model declined to help with this request."
        if stop_reason == "max_tokens":
            return (text + "\n\n(The reply was cut short.)").strip()
        return text or "(No reply text.)"

    @staticmethod
    def _error_result(block: Any, message: str) -> dict[str, Any]:
        return {"type": "tool_result", "tool_use_id": _block_attr(block, "id"), "content": message, "is_error": True}

    def _run_tool(
        self,
        session: Session,
        block: Any,
        records: list[ToolCallRecord],
        proposed: list[PendingOperation],
        created: list[PendingOperation],
    ) -> dict[str, Any]:
        name = str(_block_attr(block, "name", ""))
        raw_input = _block_attr(block, "input", {})
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            records.append(ToolCallRecord(name, "unknown", "error", "Unknown tool"))
            return self._error_result(block, f"Unknown tool '{name}'. Use only the tools provided.")

        if not isinstance(raw_input, dict):
            records.append(ToolCallRecord(name, spec.kind, "error", "Invalid arguments"))
            return self._error_result(block, "Invalid arguments: expected a JSON object.")

        try:
            params = spec.input_model.model_validate(raw_input)
            if spec.kind == "read":
                result: Any = spec.run(self._ctx, params)
                records.append(ToolCallRecord(name, "read", "ok"))
            else:
                proposal = spec.propose(self._ctx, params)
                op, is_new = store_proposal(session, spec, proposal, self._ctx)
                if is_new:
                    created.append(op)
                if op not in proposed:
                    proposed.append(op)
                records.append(ToolCallRecord(name, "write", "proposed", op.summary))
                result = {
                    "status": "awaiting_user_approval",
                    "operation_id": op.id,
                    "summary": op.summary,
                    "warnings": op.warnings,
                    "note": "NOT executed. The user is now shown this for approval. Do not say it is done.",
                }
        except ValidationError as exc:
            records.append(ToolCallRecord(name, spec.kind, "error", "Invalid arguments"))
            return self._error_result(block, _format_validation_error(exc))
        except AppError as exc:
            if exc.code in ("configuration_error",):
                raise  # a missing bridge/Claude setup should reach the user, not Claude
            records.append(ToolCallRecord(name, spec.kind, "error", exc.message))
            return self._error_result(block, f"Error ({exc.code}): {exc.message}")
        except Exception:  # noqa: BLE001 - never crash the loop on one tool
            logger.exception("tool crashed: %s", name)
            records.append(ToolCallRecord(name, spec.kind, "error", "Unexpected error"))
            return self._error_result(block, "Unexpected internal error while running this tool.")

        logger.info("tool %s (%s) ok", name, spec.kind)
        text = json.dumps(result, ensure_ascii=False, default=str)
        return {
            "type": "tool_result",
            "tool_use_id": _block_attr(block, "id"),
            "content": limit_tool_result(text, self._settings.tool_result_max_chars),
        }
