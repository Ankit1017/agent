import { type ReactNode, useEffect, useRef, useState } from "react";

export type HarnessModule = "chat" | "speech" | "agents" | "studio";
type Theme = "system" | "light" | "dark";

const modules: Array<{ id: HarnessModule; label: string; href: string }> = [
  { id: "chat", label: "Chat", href: "/" },
  { id: "speech", label: "Speech", href: "/speech" },
  { id: "agents", label: "Voice Agents", href: "/speech/agents" },
  { id: "studio", label: "Studio", href: "/studio" },
];

function storedTheme(): Theme {
  const value = localStorage.getItem("harness-theme");
  return value === "light" || value === "dark" ? value : "system";
}

function ModuleIcon({ module }: { module: HarnessModule }) {
  return (
    <svg aria-hidden="true" className="ui-icon" viewBox="0 0 24 24">
      {module === "chat" && <path d="M4 5h16v11H9l-5 4V5Z" />}
      {module === "speech" && (
        <path d="M7 10v4m5-8v12m5-8v4M4 12a8 8 0 0 0 16 0" />
      )}
      {module === "agents" && (
        <path d="M3 19c1-4 3-6 6-6s5 2 6 6M6 8a3 3 0 1 1 6 0 3 3 0 0 1-6 0Zm9 3a3 3 0 1 0 0-6m1 8c2 0 4 2 5 6" />
      )}
      {module === "studio" && <path d="M3 5h18v14H3zM10 9l6 3-6 3V9Z" />}
    </svg>
  );
}

export function AppHeader({
  current,
  title,
  status,
}: {
  current: HarnessModule;
  title: string;
  status?: ReactNode;
}) {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [density, setDensity] = useState<"comfortable" | "compact">(
    () =>
      (localStorage.getItem("harness-density") as
        "comfortable" | "compact" | null) ?? "comfortable",
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    localStorage.setItem("harness-theme", theme);
  }, [theme]);
  useEffect(() => {
    document.documentElement.dataset.density = density;
    localStorage.setItem("harness-density", density);
  }, [density]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [menuOpen]);

  const themeLabel = theme === "system" ? "System theme" : `${theme} theme`;
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="app-header">
        <a className="app-identity" href="/" aria-label="Local AI Harness home">
          <span className="app-mark">H</span>
          <span>
            <strong>Local AI Harness</strong>
            <small>{title}</small>
          </span>
        </a>
        <nav className="module-switcher" aria-label="Harness modules">
          {modules.map((item) => (
            <a
              className={item.id === current ? "active" : ""}
              href={item.href}
              aria-current={item.id === current ? "page" : undefined}
              key={item.id}
            >
              <ModuleIcon module={item.id} />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>
        <div className="app-header-actions">
          {status && <div className="header-status">{status}</div>}
          <button
            className="header-control"
            aria-label={`Density: ${density}. Change density`}
            onClick={() =>
              setDensity((value) =>
                value === "comfortable" ? "compact" : "comfortable",
              )
            }
          >
            {density === "comfortable" ? "Comfortable" : "Compact"}
          </button>
          <button
            className="header-control"
            aria-label={`Theme: ${theme}. Change theme`}
            onClick={() =>
              setTheme((value) =>
                value === "system"
                  ? "dark"
                  : value === "dark"
                    ? "light"
                    : "system",
              )
            }
          >
            {themeLabel}
          </button>
          <button
            ref={menuButton}
            className="mobile-menu-button"
            aria-expanded={menuOpen}
            aria-controls="mobile-module-menu"
            aria-label="Open module navigation"
            onClick={() => setMenuOpen((value) => !value)}
          >
            <span />
            <span />
            <span />
          </button>
        </div>
      </header>
      {menuOpen && (
        <div
          className="mobile-module-backdrop"
          onMouseDown={(event) =>
            event.currentTarget === event.target && setMenuOpen(false)
          }
        >
          <nav
            className="mobile-module-menu"
            id="mobile-module-menu"
            aria-label="Mobile harness modules"
          >
            <div className="drawer-heading">
              <strong>Switch module</strong>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  menuButton.current?.focus();
                }}
              >
                Close
              </button>
            </div>
            {modules.map((item) => (
              <a
                className={item.id === current ? "active" : ""}
                href={item.href}
                aria-current={item.id === current ? "page" : undefined}
                key={item.id}
              >
                <ModuleIcon module={item.id} />
                <span>{item.label}</span>
              </a>
            ))}
          </nav>
        </div>
      )}
    </>
  );
}

export function StatusRegion({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
  children: ReactNode;
}) {
  return (
    <div className={`ui-status ${tone}`} role="status" aria-live="polite">
      <span aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
  children: ReactNode;
}) {
  return <span className={`ui-badge ${tone}`}>{children}</span>;
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="ui-empty">
      <span className="ui-empty-mark" aria-hidden="true">
        H
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  );
}

export function AppDialog({
  title,
  children,
  actions,
  onClose,
}: {
  title: string;
  children: ReactNode;
  actions: ReactNode;
  onClose: () => void;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => event.currentTarget === event.target && onClose()}
    >
      <section
        className="modal ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ui-dialog-title"
      >
        <button
          ref={closeButton}
          className="modal-close"
          aria-label="Close dialog"
          onClick={onClose}
        >
          ×
        </button>
        <h2 id="ui-dialog-title">{title}</h2>
        <div className="ui-dialog-content">{children}</div>
        <div className="modal-actions">{actions}</div>
      </section>
    </div>
  );
}
