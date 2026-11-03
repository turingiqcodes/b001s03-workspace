// The approval panel: every proposed write waits here until the user decides.
import { useState } from "react";

import type { Busy } from "../state/useAgent";
import type { Decision, PendingOperation } from "../types/api";
import { formatDateTime, OperationDetails } from "./OperationDetails";

const TOOL_LABELS: Record<string, string> = {
  send_email: "Send email",
  create_draft: "Save draft",
  reply_email: "Send reply",
  create_reply_draft: "Save reply draft",
  forward_email: "Forward email",
  modify_email_labels: "Change labels",
  create_event: "Create event",
  update_event: "Update event",
  cancel_event: "Cancel event",
  create_folder: "Create folder",
  move_file: "Move file",
  save_email_attachments: "Save attachments to Drive",
};

interface CardProps {
  operation: PendingOperation;
  busy: Busy;
  onDecide: (operation: PendingOperation, decision: Decision, acknowledgeWarnings: boolean) => void;
}

function ApprovalCard({ operation, busy, onDecide }: CardProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const thisBusy = busy?.kind === "confirm" && busy.operationId === operation.id;
  const anyBusy = busy !== null;
  const approveBlocked = operation.requires_acknowledgement && !acknowledged;

  return (
    <article className="approval-card" aria-label={`Approval needed: ${operation.summary}`}>
      <div className="approval-kind">{TOOL_LABELS[operation.tool_name] ?? operation.tool_name}</div>
      <h3>{operation.summary}</h3>
      <OperationDetails toolName={operation.tool_name} details={operation.details} />

      {operation.warnings.length > 0 && (
        <div className="warnings" role="note">
          <strong>Please review:</strong>
          <ul>
            {operation.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          <label className="acknowledge">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              disabled={anyBusy}
            />
            I have reviewed these warnings
          </label>
        </div>
      )}

      <p className="expires">Expires {formatDateTime(operation.expires_at)}</p>
      <div className="approval-actions">
        <button
          type="button"
          onClick={() => onDecide(operation, "approve", acknowledged)}
          disabled={anyBusy || approveBlocked}
        >
          {thisBusy ? "Working…" : "Approve"}
        </button>
        <button type="button" className="danger" onClick={() => onDecide(operation, "reject", false)} disabled={anyBusy}>
          Reject
        </button>
      </div>
    </article>
  );
}

interface Props {
  operations: PendingOperation[];
  busy: Busy;
  onDecide: CardProps["onDecide"];
}

export function PendingOperations({ operations, busy, onDecide }: Props) {
  return (
    <aside className="approvals" aria-label="Pending approvals">
      <h2>Waiting for your approval</h2>
      {operations.length === 0 ? (
        <p className="muted">Nothing to approve. Emails, events and Drive changes appear here before anything happens.</p>
      ) : (
        operations.map((operation) => (
          <ApprovalCard key={operation.id} operation={operation} busy={busy} onDecide={onDecide} />
        ))
      )}
    </aside>
  );
}
