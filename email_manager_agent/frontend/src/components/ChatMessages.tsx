import { useEffect, useRef } from "react";

import type { ChatMessage } from "../state/useAgent";
import { FormattedText } from "./FormattedText";

const EXAMPLES = [
  "Find unread emails from today",
  "Summarize my latest 5 emails",
  "Find a free 30 minute slot tomorrow",
  "Find PDF files about invoices in Drive",
];

interface Props {
  messages: ChatMessage[];
  thinking: boolean;
  onExample: (text: string) => void;
}

const TOOL_STATUS_LABEL: Record<string, string> = { ok: "done", error: "failed", proposed: "needs approval" };

export function ChatMessages({ messages, thinking, onExample }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages.length, thinking]);

  if (messages.length === 0 && !thinking) {
    return (
      <div className="chat empty-state">
        <p>Ask about your email, calendar or Drive. Nothing is sent or changed until you approve it.</p>
        <div className="examples">
          {EXAMPLES.map((example) => (
            <button type="button" key={example} className="example" onClick={() => onExample(example)}>
              {example}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="chat" role="log" aria-label="Conversation">
      {messages.map((message) => (
        <div key={message.id} className={`message message-${message.role}`}>
          <div className="message-role">
            {message.role === "user" ? "You" : message.role === "assistant" ? "Agent" : message.role === "error" ? "Error" : "Update"}
          </div>
          <div className="message-text">
            {message.role === "assistant" ? <FormattedText text={message.text} /> : message.text}
          </div>
          {message.toolCalls && message.toolCalls.length > 0 && (
            <ul className="tool-calls" aria-label="Tools used">
              {message.toolCalls.map((call, index) => (
                <li key={`${call.name}-${index}`} className={`tool tool-${call.status}`}>
                  {call.name.replace(/_/g, " ")}: {TOOL_STATUS_LABEL[call.status] ?? call.status}
                  {call.status === "error" && call.message ? ` (${call.message})` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {thinking && (
        <div className="message message-assistant loading" role="status">
          <span className="spinner" aria-hidden="true" /> Working on it…
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
