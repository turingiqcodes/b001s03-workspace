"""Request and response bodies for the four API endpoints."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----- requests -----

class PromptRequest(StrictModel):
    session_id: str | None = Field(None, description="Omit on the first request; reuse the returned id after.")
    prompt: str = Field(min_length=1)


class ConfirmRequest(StrictModel):
    session_id: str
    operation_id: str = Field(pattern=r"^op_[A-Za-z0-9_-]{8,40}$")
    decision: Literal["approve", "reject"]
    acknowledge_warnings: bool = Field(False, description="Required to approve an operation that has warnings.")


class ResetRequest(StrictModel):
    session_id: str | None = None


# ----- responses -----

class ToolCallOut(BaseModel):
    name: str
    kind: str
    status: str
    message: str = ""


class PendingOperationOut(BaseModel):
    id: str
    tool_name: str
    summary: str
    details: dict[str, Any]
    warnings: list[str]
    requires_acknowledgement: bool
    created_at: str
    expires_at: str
    status: str


class PromptResponse(BaseModel):
    session_id: str
    reply: str
    steps: int
    stop_reason: str
    tool_calls: list[ToolCallOut]
    pending_operations: list[PendingOperationOut]


class ConfirmResponse(BaseModel):
    session_id: str
    operation_id: str
    status: Literal["executed", "rejected", "failed"]
    message: str
    result: Any = None
    pending_operations: list[PendingOperationOut]


class ResetResponse(BaseModel):
    session_id: str
    status: Literal["reset"] = "reset"


class ComponentStatus(BaseModel):
    configured: bool
    issues: list[str]


class LLMStatus(ComponentStatus):
    provider: str
    model: str | None
    reachable: bool | None = Field(None, description="Only checked when ?check_bridge=true.")


class BridgeStatus(ComponentStatus):
    reachable: bool | None = Field(None, description="Only checked when ?check_bridge=true.")
    account: str | None = None


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"
    llm: LLMStatus
    bridge: BridgeStatus
    session_active: bool
    pending_operations: list[PendingOperationOut]
    limits: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
