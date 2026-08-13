import { describe, expect, it } from "vitest";
import { normalizeMarkdownForDisplay, safeExternalHref } from "./markdown";

describe("Markdown presentation policy", () => {
  it("repairs legacy breaks outside code fences", () => {
    const value = "A<br>B\n```html\nA<br>B\n```";
    expect(normalizeMarkdownForDisplay(value)).toBe(
      "A\n\nB\n```html\nA<br>B\n```",
    );
  });

  it("allows only explicit HTTP source links", () => {
    expect(safeExternalHref("https://docs.example/page")).toContain(
      "docs.example",
    );
    expect(safeExternalHref("http://docs.example/page")).toContain(
      "docs.example",
    );
    expect(safeExternalHref("javascript:alert(1)")).toBeUndefined();
    expect(safeExternalHref("relative/path")).toBeUndefined();
    expect(safeExternalHref(undefined)).toBeUndefined();
  });
});
