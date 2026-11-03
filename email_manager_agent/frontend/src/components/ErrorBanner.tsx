interface Props {
  message: string | null;
  onDismiss: () => void;
}

export function ErrorBanner({ message, onDismiss }: Props) {
  if (!message) return null;
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      <button type="button" className="secondary" onClick={onDismiss} aria-label="Dismiss error">
        Dismiss
      </button>
    </div>
  );
}
