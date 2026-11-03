// Application state: chat messages, pending approvals, loading and errors.
import { useCallback, useEffect, useReducer, useRef } from "react";

import { api, ApiError } from "../api/client";
import type { Decision, PendingOperation, StatusResponse, ToolCall } from "../types/api";

export type MessageRole = "user" | "assistant" | "notice" | "error";

export interface ChatMessage {
  id: number;
  role: MessageRole;
  text: string;
  toolCalls?: ToolCall[];
}

export type Busy = { kind: "prompt" } | { kind: "confirm"; operationId: string } | { kind: "reset" } | null;

export interface AgentState {
  sessionId: string | null;
  messages: ChatMessage[];
  pending: PendingOperation[];
  busy: Busy;
  error: string | null;
  status: StatusResponse | null;
  statusError: string | null;
}

type Action =
  | { type: "add_message"; role: MessageRole; text: string; toolCalls?: ToolCall[] }
  | { type: "set_busy"; busy: Busy }
  | { type: "set_error"; error: string | null }
  | { type: "set_session"; sessionId: string | null }
  | { type: "set_pending"; pending: PendingOperation[] }
  | { type: "set_status"; status: StatusResponse | null; statusError: string | null }
  | { type: "reset"; sessionId: string };

const SESSION_KEY = "email-manager-session-id";

let nextMessageId = 1;

function readStoredSession(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function storeSession(sessionId: string | null): void {
  try {
    if (sessionId) window.sessionStorage.setItem(SESSION_KEY, sessionId);
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage can be unavailable (private mode); the session then lasts until reload.
  }
}

export function initialState(): AgentState {
  return {
    sessionId: readStoredSession(),
    messages: [],
    pending: [],
    busy: null,
    error: null,
    status: null,
    statusError: null,
  };
}

export function reducer(state: AgentState, action: Action): AgentState {
  switch (action.type) {
    case "add_message":
      return {
        ...state,
        messages: [
          ...state.messages,
          { id: nextMessageId++, role: action.role, text: action.text, toolCalls: action.toolCalls },
        ],
      };
    case "set_busy":
      return { ...state, busy: action.busy };
    case "set_error":
      return { ...state, error: action.error };
    case "set_session":
      return { ...state, sessionId: action.sessionId };
    case "set_pending":
      return { ...state, pending: action.pending };
    case "set_status":
      return { ...state, status: action.status, statusError: action.statusError };
    case "reset":
      return { ...state, sessionId: action.sessionId, messages: [], pending: [], error: null };
    default:
      return state;
  }
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Something went wrong. Please try again.";
}

export function useAgent() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);

  useEffect(() => storeSession(state.sessionId), [state.sessionId]);

  // A ref so refreshStatus stays stable and can tell if a reply is for the current session.
  const sessionRef = useRef(state.sessionId);
  sessionRef.current = state.sessionId;

  const refreshStatus = useCallback(async (checkBridge = false) => {
    const askedFor = sessionRef.current;
    try {
      const status = await api.status(askedFor, checkBridge);
      dispatch({ type: "set_status", status, statusError: null });
      if (sessionRef.current !== askedFor) return; // session changed meanwhile: ignore its pending list
      if (askedFor && !status.session_active) {
        // The backend restarted: its in-memory session (and approvals) are gone.
        dispatch({ type: "set_session", sessionId: null });
        dispatch({ type: "set_pending", pending: [] });
      } else if (askedFor) {
        dispatch({ type: "set_pending", pending: status.pending_operations });
      }
    } catch (error) {
      dispatch({ type: "set_status", status: null, statusError: errorText(error) });
    }
  }, []);

  const sendPrompt = useCallback(
    async (prompt: string): Promise<boolean> => {
      const text = prompt.trim();
      if (!text || state.busy) return false;
      dispatch({ type: "set_error", error: null });
      dispatch({ type: "add_message", role: "user", text });
      dispatch({ type: "set_busy", busy: { kind: "prompt" } });
      try {
        const response = await api.prompt({ session_id: state.sessionId, prompt: text });
        dispatch({ type: "set_session", sessionId: response.session_id });
        dispatch({ type: "add_message", role: "assistant", text: response.reply, toolCalls: response.tool_calls });
        dispatch({ type: "set_pending", pending: response.pending_operations });
        return true;
      } catch (error) {
        dispatch({ type: "add_message", role: "error", text: errorText(error) });
        return false;
      } finally {
        dispatch({ type: "set_busy", busy: null });
      }
    },
    [state.busy, state.sessionId],
  );

  const decide = useCallback(
    async (operation: PendingOperation, decision: Decision, acknowledgeWarnings: boolean) => {
      if (!state.sessionId || state.busy) return;
      dispatch({ type: "set_error", error: null });
      dispatch({ type: "set_busy", busy: { kind: "confirm", operationId: operation.id } });
      try {
        const response = await api.confirm({
          session_id: state.sessionId,
          operation_id: operation.id,
          decision,
          acknowledge_warnings: acknowledgeWarnings,
        });
        dispatch({ type: "set_pending", pending: response.pending_operations });
        dispatch({ type: "add_message", role: response.status === "failed" ? "error" : "notice", text: response.message });
      } catch (error) {
        dispatch({ type: "set_error", error: errorText(error) });
        if (error instanceof ApiError && [404, 409, 410].includes(error.status)) {
          // The operation is gone, expired or already decided: show the server's current list.
          await refreshStatus();
        }
      } finally {
        dispatch({ type: "set_busy", busy: null });
      }
    },
    [state.busy, state.sessionId, refreshStatus],
  );

  const reset = useCallback(async () => {
    if (state.busy) return;
    dispatch({ type: "set_busy", busy: { kind: "reset" } });
    try {
      const response = await api.reset(state.sessionId);
      dispatch({ type: "reset", sessionId: response.session_id });
    } catch (error) {
      dispatch({ type: "set_error", error: errorText(error) });
    } finally {
      dispatch({ type: "set_busy", busy: null });
    }
  }, [state.busy, state.sessionId]);

  const dismissError = useCallback(() => dispatch({ type: "set_error", error: null }), []);

  return { state, sendPrompt, decide, reset, refreshStatus, dismissError };
}
