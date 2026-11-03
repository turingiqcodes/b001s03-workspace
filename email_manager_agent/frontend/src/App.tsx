import { useEffect, useState } from "react";

import { ChatMessages } from "./components/ChatMessages";
import { ErrorBanner } from "./components/ErrorBanner";
import { PendingOperations } from "./components/PendingOperations";
import { PromptInput } from "./components/PromptInput";
import { StatusBar } from "./components/StatusBar";
import { useAgent } from "./state/useAgent";

const STATUS_REFRESH_MS = 30_000;

export default function App() {
  const { state, sendPrompt, decide, reset, refreshStatus, dismissError } = useAgent();
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), STATUS_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  const maxLength = Number(state.status?.limits.prompt_max_chars ?? 4000);

  return (
    <div className="app">
      <StatusBar
        status={state.status}
        statusError={state.statusError}
        onCheckBridge={() => void refreshStatus(true)}
        onReset={() => void reset()}
        resetDisabled={state.busy !== null}
      />
      <ErrorBanner message={state.error} onDismiss={dismissError} />
      <main className="layout">
        <section className="conversation">
          <ChatMessages messages={state.messages} thinking={state.busy?.kind === "prompt"} onExample={setDraft} />
          <PromptInput
            disabled={state.busy !== null}
            maxLength={maxLength}
            value={draft}
            onChange={setDraft}
            onSend={sendPrompt}
          />
        </section>
        <PendingOperations operations={state.pending} busy={state.busy} onDecide={(op, d, ack) => void decide(op, d, ack)} />
      </main>
    </div>
  );
}
