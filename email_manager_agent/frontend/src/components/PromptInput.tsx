import { useState, type FormEvent, type KeyboardEvent } from "react";

interface Props {
  disabled: boolean;
  maxLength: number;
  value?: string;
  onChange?: (value: string) => void;
  onSend: (prompt: string) => Promise<boolean>;
}

export function PromptInput({ disabled, maxLength, value, onChange, onSend }: Props) {
  const [ownText, setOwnText] = useState("");
  const text = value ?? ownText;
  const setText = onChange ?? setOwnText;

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!text.trim() || disabled) return;
    const sent = await onSend(text);
    if (sent) setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form className="prompt-input" onSubmit={submit}>
      <label htmlFor="prompt" className="visually-hidden">
        Message
      </label>
      <textarea
        id="prompt"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="e.g. Draft a reply to the latest email from Alice"
        rows={2}
        maxLength={maxLength}
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        {disabled ? "Working…" : "Send"}
      </button>
    </form>
  );
}
