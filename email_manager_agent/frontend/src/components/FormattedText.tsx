// Shows model replies as text, turning **bold** into <strong>.
// Builds React elements (never HTML strings), so email content can't inject markup.
import { Fragment } from "react";

const BOLD = /\*\*(.+?)\*\*/g;

export function FormattedText({ text }: { text: string }) {
  const lines = text.split("\n").map((line) => line.replace(/^#{1,6}\s+/, "")); // drop heading marks
  return (
    <>
      {lines.map((line, lineIndex) => (
        <Fragment key={lineIndex}>
          {lineIndex > 0 && "\n"}
          {line.split(BOLD).map((part, partIndex) =>
            partIndex % 2 === 1 ? <strong key={partIndex}>{part}</strong> : part,
          )}
        </Fragment>
      ))}
    </>
  );
}
