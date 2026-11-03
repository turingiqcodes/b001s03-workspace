import { vi } from "vitest";

import type { PendingOperation, StatusResponse } from "../types/api";

export const okStatus: StatusResponse = {
  status: "ok",
  llm: { configured: true, issues: [], provider: "ollama", model: "qwen3:8b", reachable: null },
  bridge: { configured: true, issues: [], reachable: null, account: null },
  session_active: true,
  pending_operations: [],
  limits: { prompt_max_chars: 4000 },
};

export function pendingEmail(overrides: Partial<PendingOperation> = {}): PendingOperation {
  return {
    id: "op_abc123456789",
    tool_name: "send_email",
    summary: 'Send email to bob@example.com: "Lunch"',
    details: { to: ["bob@example.com"], cc: [], bcc: [], subject: "Lunch", body: "Noon tomorrow?" },
    warnings: [],
    requires_acknowledgement: false,
    created_at: "2026-11-09T08:00:00+00:00",
    expires_at: "2026-11-09T08:10:00+00:00",
    status: "pending",
    ...overrides,
  };
}

type Handler = (body: unknown, url: string) => { status?: number; json: unknown } | Promise<{ status?: number; json: unknown }>;

/** Install a fetch mock. Keys look like "POST /api/prompt"; GET keys ignore the query string. */
export function mockFetch(handlers: Record<string, Handler>) {
  const calls: { key: string; url: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const key = `${method} ${url.split("?")[0]}`;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ key, url, body });
    const handler = handlers[key];
    if (!handler) throw new Error(`Unexpected request ${key}`);
    const result = await handler(body, url);
    return new Response(JSON.stringify(result.json), {
      status: result.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}
