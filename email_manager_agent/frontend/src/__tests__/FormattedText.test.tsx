import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FormattedText } from "../components/FormattedText";

describe("FormattedText", () => {
  it("turns **bold** into strong and drops heading marks", () => {
    const { container } = render(<div><FormattedText text={"## Inbox\n**Subject:** Q3 report"} /></div>);
    expect(container.querySelector("strong")?.textContent).toBe("Subject:");
    expect(container.textContent).toBe("Inbox\nSubject: Q3 report");
  });

  it("never renders HTML from the text", () => {
    const { container } = render(<div><FormattedText text={'<img src=x onerror="alert(1)"> **<b>hi</b>**'} /></div>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("<b>hi</b>");
  });
});
