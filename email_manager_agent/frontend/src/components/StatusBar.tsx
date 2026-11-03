import type { StatusResponse } from "../types/api";

interface Props {
  status: StatusResponse | null;
  statusError: string | null;
  onCheckBridge: () => void;
  onReset: () => void;
  resetDisabled: boolean;
}

function Dot({ state }: { state: "ok" | "warn" | "bad" }) {
  return <span className={`dot dot-${state}`} aria-hidden="true" />;
}

export function StatusBar({ status, statusError, onCheckBridge, onReset, resetDisabled }: Props) {
  const issues = status ? [...status.llm.issues, ...status.bridge.issues] : [];
  const llm = status?.llm;
  const llmState = !llm?.configured || llm.reachable === false ? "bad" : llm.reachable ? "ok" : "warn";
  const llmLabel = !llm?.configured
    ? "Model not configured"
    : llm.reachable === false
      ? `Model unreachable (${llm.provider})`
      : `Model ${llm.reachable ? "ready" : "configured"} (${llm.provider} · ${llm.model})`;
  const bridgeState = !status?.bridge.configured ? "bad" : status.bridge.reachable === false ? "bad" : status.bridge.reachable ? "ok" : "warn";
  const bridgeLabel = !status?.bridge.configured
    ? "Google bridge not configured"
    : status.bridge.reachable === null
      ? "Google bridge configured (not checked)"
      : status.bridge.reachable
        ? `Google connected${status.bridge.account ? ` (${status.bridge.account})` : ""}`
        : "Google bridge unreachable";

  return (
    <header className="status-bar">
      <h1>Email Manager Agent</h1>
      <div className="status-items" aria-live="polite">
        {statusError ? (
          <span className="status-item" role="status">
            <Dot state="bad" /> Backend offline: {statusError}
          </span>
        ) : status ? (
          <>
            <span className="status-item" role="status">
              <Dot state={llmState} />
              {llmLabel}
            </span>
            <span className="status-item">
              <Dot state={bridgeState} /> {bridgeLabel}
            </span>
          </>
        ) : (
          <span className="status-item">Connecting…</span>
        )}
      </div>
      <div className="status-actions">
        <button type="button" className="secondary" onClick={onCheckBridge}>
          Check connection
        </button>
        <button type="button" className="secondary" onClick={onReset} disabled={resetDisabled}>
          New conversation
        </button>
      </div>
      {issues.length > 0 && (
        <ul className="config-issues" aria-label="Configuration problems">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </header>
  );
}
