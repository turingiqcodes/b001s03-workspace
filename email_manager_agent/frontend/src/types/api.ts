// TypeScript mirrors of backend/app/schemas.py. Keep the two in sync.

export interface ToolCall {
  name: string;
  kind: "read" | "write" | "unknown" | string;
  status: "ok" | "error" | "proposed" | string;
  message: string;
}

export interface PendingOperation {
  id: string;
  tool_name: string;
  summary: string;
  details: Record<string, unknown>;
  warnings: string[];
  requires_acknowledgement: boolean;
  created_at: string;
  expires_at: string;
  status: string;
}

export interface PromptRequest {
  session_id?: string | null;
  prompt: string;
}

export interface PromptResponse {
  session_id: string;
  reply: string;
  steps: number;
  stop_reason: string;
  tool_calls: ToolCall[];
  pending_operations: PendingOperation[];
}

export type Decision = "approve" | "reject";

export interface ConfirmRequest {
  session_id: string;
  operation_id: string;
  decision: Decision;
  acknowledge_warnings: boolean;
}

export interface ConfirmResponse {
  session_id: string;
  operation_id: string;
  status: "executed" | "rejected" | "failed";
  message: string;
  result: unknown;
  pending_operations: PendingOperation[];
}

export interface ResetResponse {
  session_id: string;
  status: "reset";
}

export interface ComponentStatus {
  configured: boolean;
  issues: string[];
}

export interface StatusResponse {
  status: "ok";
  llm: ComponentStatus & { provider: string; model: string | null; reachable: boolean | null };
  bridge: ComponentStatus & { reachable: boolean | null; account: string | null };
  session_active: boolean;
  pending_operations: PendingOperation[];
  limits: Record<string, string | number>;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}
