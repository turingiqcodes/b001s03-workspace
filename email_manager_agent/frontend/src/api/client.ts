// All calls to the FastAPI backend. Paths are relative: the Vite dev server proxies /api.
import type {
  ApiErrorBody,
  ConfirmRequest,
  ConfirmResponse,
  PromptRequest,
  PromptResponse,
  ResetResponse,
  StatusResponse,
} from "../types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Cannot reach the backend. Is it running on port 8000?", "network_error", 0);
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const error = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      error?.message ?? `The backend answered with HTTP ${response.status}.`,
      error?.code ?? "http_error",
      response.status,
    );
  }
  if (body === null) {
    throw new ApiError("The backend returned an empty or invalid response.", "invalid_response", response.status);
  }
  return body as T;
}

function post<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(payload) });
}

export const api = {
  status(sessionId: string | null, checkBridge = false): Promise<StatusResponse> {
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (checkBridge) params.set("check_bridge", "true");
    const query = params.toString();
    return request<StatusResponse>(`/api/status${query ? `?${query}` : ""}`);
  },
  prompt(body: PromptRequest): Promise<PromptResponse> {
    return post<PromptResponse>("/api/prompt", body);
  },
  confirm(body: ConfirmRequest): Promise<ConfirmResponse> {
    return post<ConfirmResponse>("/api/confirm", body);
  },
  reset(sessionId: string | null): Promise<ResetResponse> {
    return post<ResetResponse>("/api/reset", { session_id: sessionId });
  },
};
