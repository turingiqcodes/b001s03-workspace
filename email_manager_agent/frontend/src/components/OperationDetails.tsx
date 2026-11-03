// Readable details for each kind of proposed write operation.
import type { ReactNode } from "react";

type Details = Record<string, unknown>;

export function formatDateTime(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function asObject(value: unknown): Details {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Details) : {};
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  if (children === "" || children === null || children === undefined) return null;
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Body({ text }: { text: unknown }) {
  if (typeof text !== "string" || !text) return null;
  return <pre className="email-body">{text}</pre>;
}

function EmailHeader({ email }: { email: Details }) {
  return (
    <>
      <Field label="Original from">{String(email.from ?? "")}</Field>
      <Field label="Original subject">{String(email.subject ?? "")}</Field>
      <Field label="Original date">{formatDateTime(email.date)}</Field>
    </>
  );
}

function Conflicts({ conflicts }: { conflicts: unknown }) {
  const items = Array.isArray(conflicts) ? (conflicts as Details[]) : [];
  if (items.length === 0) return null;
  return (
    <Field label="Conflicts">
      <ul>
        {items.map((c) => (
          <li key={String(c.event_id)}>
            {String(c.title)} ({formatDateTime(c.start)} – {formatDateTime(c.end)})
          </li>
        ))}
      </ul>
    </Field>
  );
}

function EventTimes({ start, end }: { start: unknown; end: unknown }) {
  return (
    <>
      <Field label="Starts">{formatDateTime(start)}</Field>
      <Field label="Ends">{formatDateTime(end)}</Field>
    </>
  );
}

export function OperationDetails({ toolName, details }: { toolName: string; details: Details }) {
  switch (toolName) {
    case "send_email":
    case "create_draft":
      return (
        <dl>
          <Field label="To">{asList(details.to).join(", ")}</Field>
          <Field label="Cc">{asList(details.cc).join(", ")}</Field>
          <Field label="Bcc">{asList(details.bcc).join(", ")}</Field>
          <Field label="Subject">{String(details.subject ?? "")}</Field>
          <Body text={details.body} />
        </dl>
      );
    case "reply_email":
    case "create_reply_draft":
      return (
        <dl>
          <EmailHeader email={asObject(details.in_reply_to)} />
          <Field label="Reply to">{details.reply_all ? "Everyone" : "Sender only"}</Field>
          <Body text={details.body} />
        </dl>
      );
    case "forward_email":
      return (
        <dl>
          <EmailHeader email={asObject(details.original)} />
          <Field label="Forward to">{asList(details.to).join(", ")}</Field>
          <Body text={details.note} />
        </dl>
      );
    case "modify_email_labels":
      return (
        <dl>
          <EmailHeader email={asObject(details.email)} />
          <Field label="Add labels">{asList(details.add_labels).join(", ")}</Field>
          <Field label="Remove labels">{asList(details.remove_labels).join(", ")}</Field>
          <Field label="Read status">{details.mark_read === true ? "Mark as read" : details.mark_read === false ? "Mark as unread" : ""}</Field>
        </dl>
      );
    case "create_event":
      return (
        <dl>
          <Field label="Title">{String(details.title ?? "")}</Field>
          <EventTimes start={details.start} end={details.end} />
          <Field label="Location">{String(details.location ?? "")}</Field>
          <Field label="Guests">{asList(details.attendees).join(", ")}</Field>
          <Field label="Invitations">{asList(details.attendees).length ? (details.send_invites ? "Will be sent" : "Not sent") : ""}</Field>
          <Body text={details.description} />
          <Conflicts conflicts={details.conflicts} />
        </dl>
      );
    case "update_event": {
      const current = asObject(details.current);
      const changes = asObject(details.changes);
      return (
        <dl>
          <Field label="Event">{String(current.title ?? "")}</Field>
          <Field label="Currently">{`${formatDateTime(current.start)} – ${formatDateTime(current.end)}`}</Field>
          {"start" in changes && <Field label="New time">{`${formatDateTime(changes.start)} – ${formatDateTime(changes.end)}`}</Field>}
          <Field label="New title">{String(changes.title ?? "")}</Field>
          <Field label="New location">{String(changes.location ?? "")}</Field>
          <Field label="Add guests">{asList(changes.add_attendees).join(", ")}</Field>
          <Body text={changes.description} />
          <Conflicts conflicts={details.conflicts} />
        </dl>
      );
    }
    case "cancel_event": {
      const event = asObject(details.event);
      return (
        <dl>
          <Field label="Event">{String(event.title ?? "")}</Field>
          <EventTimes start={event.start} end={event.end} />
          <Field label="Guests">{asList(event.attendees).join(", ")}</Field>
        </dl>
      );
    }
    case "create_folder":
      return (
        <dl>
          <Field label="Folder name">{String(details.name ?? "")}</Field>
        </dl>
      );
    case "move_file":
      return (
        <dl>
          <Field label="File">{String(asObject(details.file).name ?? "")}</Field>
          <Field label="Move to">{String(asObject(details.destination).name ?? "")}</Field>
        </dl>
      );
    case "save_email_attachments": {
      const save = Array.isArray(details.will_save) ? (details.will_save as Details[]) : [];
      const skip = Array.isArray(details.will_skip) ? (details.will_skip as Details[]) : [];
      return (
        <dl>
          <Field label="From email">{String(details.email_subject ?? "")}</Field>
          <Field label="Drive folder">{String(details.folder_name ?? "")}</Field>
          <Field label="Will save">
            <ul>
              {save.map((a) => (
                <li key={String(a.name)}>
                  {String(a.name)} ({Math.ceil(Number(a.size) / 1024)} KB)
                </li>
              ))}
            </ul>
          </Field>
          {skip.length > 0 && (
            <Field label="Will skip">
              <ul>
                {skip.map((a) => (
                  <li key={String(a.name)}>
                    {String(a.name)}: {String(a.reason)}
                  </li>
                ))}
              </ul>
            </Field>
          )}
        </dl>
      );
    }
    default:
      return <pre className="email-body">{JSON.stringify(details, null, 2)}</pre>;
  }
}
