import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../api/client";
import { mockFetch } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("turns error bodies into ApiError", async () => {
    mockFetch({ "POST /api/confirm": () => ({ status: 409, json: { error: { code: "approval_error", message: "already executed" } } }) });
    const error = await api.confirm({ session_id: "s", operation_id: "op_x", decision: "approve", acknowledge_warnings: false }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "approval_error", status: 409, message: "already executed" });
  });

  it("handles responses without JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>", { status: 502 })));
    await expect(api.status(null)).rejects.toMatchObject({ code: "http_error", status: 502 });
  });

  it("builds the status query", async () => {
    const calls = mockFetch({ "GET /api/status": () => ({ json: {} }) });
    await api.status("abc", true);
    expect(calls[0].url).toBe("/api/status?session_id=abc&check_bridge=true");
  });

  it("sends reset with the session id", async () => {
    const calls = mockFetch({ "POST /api/reset": () => ({ json: { session_id: "n", status: "reset" } }) });
    await api.reset("old");
    expect(calls[0].body).toEqual({ session_id: "old" });
  });
});
