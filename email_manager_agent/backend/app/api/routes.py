"""The four API endpoints: status, prompt, confirm and reset.

Handlers are plain `def` functions: FastAPI runs them in a worker thread, so the
blocking Claude and bridge calls do not stall the server.
"""
import logging

from fastapi import APIRouter, Depends, Query

from app.agent.agent_loop import Agent
from app.approvals import decide
from app.dependencies import Container, get_container
from app.errors import AppError, ValidationFailed
from app.schemas import (
    BridgeStatus,
    LLMStatus,
    ConfirmRequest,
    ConfirmResponse,
    PromptRequest,
    PromptResponse,
    ResetRequest,
    ResetResponse,
    StatusResponse,
)
from app.sessions import Session, SessionGuard

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _open_operations(session: Session) -> list[dict]:
    return [op.public() for op in session.open_operations()]


@router.get("/status", response_model=StatusResponse)
def get_status(
    session_id: str | None = Query(None),
    check_bridge: bool = Query(False, description="Also ping the Google bridge and the model server."),
    container: Container = Depends(get_container),
) -> StatusResponse:
    settings = container.settings
    llm_issues = settings.llm_config_issues()
    llm = LLMStatus(
        configured=not llm_issues, issues=llm_issues, provider=settings.llm_provider, model=settings.active_model
    )
    check_llm = getattr(container.model, "check", None)
    if check_bridge and not llm_issues and check_llm is not None:
        try:
            check_llm()
            llm.reachable = True
        except AppError as exc:
            llm.reachable = False
            llm.issues = [exc.message]
    bridge_issues = settings.bridge_config_issues()
    bridge = BridgeStatus(configured=not bridge_issues, issues=bridge_issues)
    if check_bridge and not bridge_issues:
        try:
            pong = container.bridge.call("ping", {})
            bridge.reachable = True
            if isinstance(pong, dict):
                bridge.account = pong.get("account")
        except AppError as exc:
            bridge.reachable = False
            bridge.issues = [exc.message]

    session = container.sessions.peek(session_id)
    if session:
        session.expire_operations(container.now())
    return StatusResponse(
        llm=llm,
        bridge=bridge,
        session_active=session is not None,
        pending_operations=_open_operations(session) if session else [],
        limits={
            "max_agent_steps": settings.max_agent_steps,
            "prompt_max_chars": settings.prompt_max_chars,
            "bridge_timeout_seconds": settings.bridge_timeout_seconds,
            "approval_ttl_seconds": settings.approval_ttl_seconds,
            "timezone": settings.user_timezone,
            "working_hours": f"{settings.work_day_start:%H:%M}-{settings.work_day_end:%H:%M}",
        },
    )


@router.post("/prompt", response_model=PromptResponse)
def post_prompt(body: PromptRequest, container: Container = Depends(get_container)) -> PromptResponse:
    prompt = body.prompt.strip()
    if not prompt:
        raise ValidationFailed("The prompt is empty.")
    if len(prompt) > container.settings.prompt_max_chars:
        raise ValidationFailed(f"The prompt is too long ({len(prompt)} characters; limit {container.settings.prompt_max_chars}).")

    session = container.sessions.get_or_create(body.session_id)
    with SessionGuard(session):
        agent = Agent(container.model, container.service_context)
        result = agent.run(session, prompt)
        session.expire_operations(container.now())
        return PromptResponse(
            session_id=session.id,
            reply=result.reply,
            steps=result.steps,
            stop_reason=result.stop_reason,
            tool_calls=[record.public() for record in result.tool_calls],
            pending_operations=_open_operations(session),
        )


@router.post("/confirm", response_model=ConfirmResponse)
def post_confirm(body: ConfirmRequest, container: Container = Depends(get_container)) -> ConfirmResponse:
    session = container.sessions.get(body.session_id)  # never creates a session
    with SessionGuard(session):
        op = decide(session, body.operation_id, body.decision, body.acknowledge_warnings, container.service_context)
        if op.status == "executed":
            message = f"Done: {op.summary}"
        elif op.status == "rejected":
            message = f"Rejected. Nothing was changed: {op.summary}"
        else:
            message = f"Failed: {op.error or 'unknown error'}"
        return ConfirmResponse(
            session_id=session.id,
            operation_id=op.id,
            status=op.status,
            message=message,
            result=op.result,
            pending_operations=_open_operations(session),
        )


@router.post("/reset", response_model=ResetResponse)
def post_reset(body: ResetRequest, container: Container = Depends(get_container)) -> ResetResponse:
    existing = container.sessions.peek(body.session_id)
    if existing is not None:
        with SessionGuard(existing):
            pass  # make sure no prompt/confirm is mid-flight before forgetting the session
    new_session = container.sessions.reset(body.session_id)
    return ResetResponse(session_id=new_session.id)
