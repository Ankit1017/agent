import { isValidElement, type ReactNode } from "react";
import type { ProgressEvent } from "./types";

export function statusIcon(status: ProgressEvent["status"]): string {
  return { success: "OK", error: "!", warning: "!", started: "…" }[status];
}

export function initialTheme(): "system" | "dark" | "light" {
  const saved = localStorage.getItem("harness-theme");
  return saved === "dark" || saved === "light" || saved === "system"
    ? saved
    : "system";
}

export function nextTheme(
  value: "system" | "dark" | "light",
): "system" | "dark" | "light" {
  return value === "system" ? "dark" : value === "dark" ? "light" : "system";
}

export function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  return isValidElement<{ children?: ReactNode }>(node)
    ? nodeText(node.props.children)
    : "";
}

export function codeLanguage(node: ReactNode): string {
  if (!isValidElement<{ className?: string }>(node)) return "text";
  return node.props.className?.match(/language-([\w-]+)/)?.[1] ?? "text";
}

export function upsertEvent(
  values: ProgressEvent[],
  event: ProgressEvent,
): ProgressEvent[] {
  const index = values.findIndex((item) => item.sequence === event.sequence);
  if (index < 0) return [...values, event];
  return values.map((item, position) => (position === index ? event : item));
}
