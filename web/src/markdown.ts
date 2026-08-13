export function normalizeMarkdownForDisplay(value: string): string {
  const lines = value
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n");
  const output: string[] = [];
  let fenced = false;
  for (const original of lines) {
    let line = original;
    if (line.trimStart().startsWith("```")) {
      fenced = !fenced;
      output.push(line.trimEnd());
      continue;
    }
    if (!fenced && /<br\s*\/?>/i.test(line)) {
      line = line.includes("|")
        ? line.replace(/<br\s*\/?>/gi, "; ")
        : line.replace(/<br\s*\/?>/gi, "\n\n");
    }
    output.push(...line.split("\n").map((part) => part.trimEnd()));
  }
  return output
    .join("\n")
    .replace(/\n{4,}/g, "\n\n\n")
    .trim();
}

export function safeExternalHref(
  value: string | undefined,
): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:"
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}
