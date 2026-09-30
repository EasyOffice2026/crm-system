import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ArrowUpRight, Loader2, X } from "lucide-react";
import { errorMessage } from "./api";

export function Badge({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Empty({
  title = "Nothing here yet",
  detail = "Create your first record to get started.",
}: {
  title?: string;
  detail?: string;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">✦</span>
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}
export function Field({
  label,
  children,
  full = false,
}: {
  label: string;
  children: ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`field ${full ? "full" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}
export function Stat({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint: string;
  icon: ReactNode;
}) {
  return (
    <article className="stat">
      <div className="stat-top">
        <span>{label}</span>
        <div className="stat-icon">{icon}</div>
      </div>
      <strong>{value}</strong>
      <div className="stat-hint">
        <ArrowUpRight size={14} />
        {hint}
      </div>
    </article>
  );
}
export function Modal({
  title,
  subtitle,
  children,
  close,
  save,
  submitLabel = "Save",
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  close: () => void;
  save?: (form: FormData) => Promise<void>;
  submitLabel?: string;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    ref.current
      ?.querySelector<HTMLElement>("input, select, textarea, button")
      ?.focus();
    function key(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) close();
      if (event.key === "Tab") {
        const items = [
          ...(ref.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]",
          ) ?? []),
        ];
        const first = items[0];
        const last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      before?.focus();
    };
  }, [close, busy]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!save || busy) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await save(form);
      close();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`modal ${wide ? "wide" : ""}`}
      >
        <header>
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="Close dialog"
            disabled={busy}
            onClick={close}
          >
            <X size={20} />
          </button>
        </header>
        <form onSubmit={submit}>
          <fieldset disabled={busy} className="modal-fields">
            <div className="modal-body">
              {children}
              {error && (
                <div className="error" role="alert">
                  {error}
                </div>
              )}
            </div>
          </fieldset>
          {save && (
            <footer>
              <button
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={close}
              >
                Cancel
              </button>
              <button className="button" disabled={busy}>
                {busy && <Loader2 className="spin" size={16} />}
                {busy ? "Saving…" : submitLabel}
              </button>
            </footer>
          )}
        </form>
      </div>
    </div>
  );
}
