import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { deferred, mockFetch, okStatus, pendingEmail } from "./helpers";

beforeEach(() => window.sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

async function typeAndSend(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Message"), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
  return user;
}

describe("chat", () => {
  it("sends the prompt, shows loading, then the reply", async () => {
    const reply = deferred<{ json: unknown }>();
    const calls = mockFetch({
      "GET /api/status": () => ({ json: okStatus }),
      "POST /api/prompt": () => reply.promise,
    });
    render(<App />);
    await typeAndSend("Find unread emails");

    expect(within(screen.getByRole("log")).getByText("Find unread emails")).toBeInTheDocument();
    expect(screen.getByText(/Working on it/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();

    reply.resolve({ json: {
      session_id: "s".repeat(32), reply: "You have 2 unread emails.", steps: 2, stop_reason: "end_turn",
      tool_calls: [{ name: "search_emails", kind: "read", status: "ok", message: "" }], pending_operations: [],
    } });
    expect(await screen.findByText("You have 2 unread emails.")).toBeInTheDocument();
    expect(screen.getByText("search emails: done")).toBeInTheDocument();
    expect(screen.queryByText(/Working on it/)).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/prompt")?.body).toEqual({ session_id: null, prompt: "Find unread emails" });
  });

  it("reuses the session id on the next prompt", async () => {
    const calls = mockFetch({
      "GET /api/status": () => ({ json: okStatus }),
      "POST /api/prompt": () => ({ json: {
        session_id: "S".repeat(32), reply: "ok", steps: 1, stop_reason: "end_turn", tool_calls: [], pending_operations: [],
      } }),
    });
    render(<App />);
    await typeAndSend("one");
    await screen.findByText("ok");
    await typeAndSend("two");
    await waitFor(() => expect(calls.filter((c) => c.key === "POST /api/prompt")).toHaveLength(2));
    expect(calls.filter((c) => c.key === "POST /api/prompt")[1].body).toEqual({ session_id: "S".repeat(32), prompt: "two" });
  });

  it("shows backend errors readably", async () => {
    mockFetch({
      "GET /api/status": () => ({ json: okStatus }),
      "POST /api/prompt": () => ({ status: 503, json: { error: { code: "configuration_error", message: "Cannot connect to Ollama at http://127.0.0.1:11434. Start it with 'ollama serve'." } } }),
    });
    render(<App />);
    await typeAndSend("hello");
    expect(await screen.findByText(/Cannot connect to Ollama/)).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });

  it("shows an offline message when the backend cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<App />);
    expect(await screen.findByText(/Backend offline: Cannot reach the backend/)).toBeInTheDocument();
  });

  it("fills the input from an example", async () => {
    mockFetch({ "GET /api/status": () => ({ json: okStatus }) });
    render(<App />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Find unread emails from today" }));
    expect(screen.getByLabelText("Message")).toHaveValue("Find unread emails from today");
  });
});

describe("status", () => {
  it("lists configuration problems", async () => {
    mockFetch({ "GET /api/status": () => ({ json: {
      ...okStatus,
      llm: { configured: false, issues: ["OLLAMA_MODEL is not set."], provider: "ollama", model: null, reachable: null },
      bridge: { configured: false, issues: ["APPS_SCRIPT_URL is not set."], reachable: null, account: null },
    } }) });
    render(<App />);
    const issues = await screen.findByLabelText("Configuration problems");
    expect(within(issues).getByText("OLLAMA_MODEL is not set.")).toBeInTheDocument();
    expect(screen.getByText("Model not configured")).toBeInTheDocument();
    expect(screen.getByText("Google bridge not configured")).toBeInTheDocument();
  });
});

describe("approvals", () => {
  function setup(confirm: (body: unknown) => { status?: number; json: unknown } | Promise<{ status?: number; json: unknown }>, operation = pendingEmail()) {
    return mockFetch({
      "GET /api/status": () => ({ json: okStatus }),
      "POST /api/prompt": () => ({ json: {
        session_id: "S".repeat(32), reply: "I've prepared the email. Please approve it.", steps: 2, stop_reason: "end_turn",
        tool_calls: [{ name: "send_email", kind: "write", status: "proposed", message: operation.summary }],
        pending_operations: [operation],
      } }),
      "POST /api/confirm": confirm,
    });
  }

  it("shows the proposed email and only reports success after the backend confirms", async () => {
    const confirmReply = deferred<{ json: unknown }>();
    const calls = setup(() => confirmReply.promise);
    render(<App />);
    const user = await typeAndSend("email bob");

    const card = await screen.findByRole("article", { name: /Approval needed/ });
    expect(within(card).getByText("bob@example.com")).toBeInTheDocument();
    expect(within(card).getByText("Noon tomorrow?")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Approve" }));
    expect(within(card).getByRole("button", { name: "Working…" })).toBeDisabled();
    expect(screen.queryByText(/^Done:/)).not.toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/confirm")?.body).toEqual({
      session_id: "S".repeat(32), operation_id: "op_abc123456789", decision: "approve", acknowledge_warnings: false,
    });

    confirmReply.resolve({ json: {
      session_id: "S".repeat(32), operation_id: "op_abc123456789", status: "executed",
      message: 'Done: Send email to bob@example.com: "Lunch"', result: { sent: true }, pending_operations: [],
    } });
    expect(await screen.findByText(/^Done: Send email/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /Approval needed/ })).not.toBeInTheDocument();
  });

  it("rejects an operation", async () => {
    const calls = setup(() => ({ json: {
      session_id: "S".repeat(32), operation_id: "op_abc123456789", status: "rejected",
      message: "Rejected. Nothing was changed: x", result: null, pending_operations: [],
    } }));
    render(<App />);
    const user = await typeAndSend("email bob");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText(/Rejected. Nothing was changed/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /api/confirm")?.body).toMatchObject({ decision: "reject" });
  });

  it("requires acknowledging warnings before approving", async () => {
    const operation = pendingEmail({
      tool_name: "create_event", summary: 'Create "Sync"',
      details: { title: "Sync", start: "2026-11-14T10:00:00+00:00", end: "2026-11-14T10:30:00+00:00", attendees: [],
                 conflicts: [{ event_id: "e1", title: "Standup", start: "2026-11-14T10:00:00Z", end: "2026-11-14T10:15:00Z" }] },
      warnings: ["Conflicts with 1 existing event(s): \"Standup\"."], requires_acknowledgement: true,
    });
    const calls = setup(() => ({ json: {
      session_id: "S".repeat(32), operation_id: operation.id, status: "executed", message: "Done", result: {}, pending_operations: [],
    } }), operation);
    render(<App />);
    const user = await typeAndSend("schedule");
    const card = await screen.findByRole("article", { name: /Approval needed/ });
    expect(within(card).getByText(/Standup \(/)).toBeInTheDocument();
    const approve = within(card).getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    await user.click(within(card).getByLabelText("I have reviewed these warnings"));
    expect(approve).toBeEnabled();
    await user.click(approve);
    await waitFor(() => expect(calls.find((c) => c.key === "POST /api/confirm")?.body).toMatchObject({ acknowledge_warnings: true }));
  });

  it("shows a failed operation as an error", async () => {
    setup(() => ({ json: {
      session_id: "S".repeat(32), operation_id: "op_abc123456789", status: "failed",
      message: "Failed: Google did not answer", result: null, pending_operations: [],
    } }));
    render(<App />);
    const user = await typeAndSend("email bob");
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Failed: Google did not answer")).toBeInTheDocument();
  });

  it("shows the server's reason when an approval is refused", async () => {
    setup(() => ({ status: 410, json: { error: { code: "approval_expired", message: "This approval request expired." } } }));
    render(<App />);
    const user = await typeAndSend("email bob");
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This approval request expired.");
  });
});

describe("model status", () => {
  it("shows the provider and model once checked", async () => {
    mockFetch({ "GET /api/status": () => ({ json: { ...okStatus, llm: { ...okStatus.llm, reachable: true } } }) });
    render(<App />);
    expect(await screen.findByText("Model ready (ollama · qwen3:8b)")).toBeInTheDocument();
  });
});
