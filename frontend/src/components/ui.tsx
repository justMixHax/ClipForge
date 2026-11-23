// Small, dependency-free interface primitives in the shadcn/ui spirit: plain components that live in the
// repo and are styled with Tailwind tokens from index.css.
import { Loader2, X } from "lucide-react";
import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { cx } from "../lib/format";

// ---------------------------------------------------------------------------------------------- Button
type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "good";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-[#1a0d05] font-semibold hover:bg-accent-soft shadow-[0_0_0_1px_rgba(255,122,61,.35),0_8px_24px_-12px_rgba(255,122,61,.7)]",
  secondary: "bg-raised text-ink border border-line hover:bg-hover hover:border-line-strong",
  ghost: "text-muted hover:text-ink hover:bg-hover",
  danger: "bg-bad/10 text-bad border border-bad/30 hover:bg-bad/20",
  good: "bg-good/10 text-good border border-good/30 hover:bg-good/20",
};
const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5 text-[13px] gap-1.5 rounded-md",
  md: "h-9 px-3.5 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-[15px] gap-2 rounded-xl",
  icon: "h-8 w-8 rounded-md justify-center",
};

export function Button({
  variant = "secondary", size = "md", busy = false, className, children, disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={cx(
        "inline-flex select-none items-center whitespace-nowrap transition-colors duration-150 disabled:opacity-50",
        BUTTON_VARIANTS[variant], BUTTON_SIZES[size], className,
      )}
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------------------- Card / Badge
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cx("rounded-2xl border border-line bg-panel", className)}>{children}</section>;
}

export function CardHeader({ title, hint, action, icon }: { title: string; hint?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <header className="flex items-start justify-between gap-3 px-5 pt-4 pb-3">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
          {icon}
          {title}
        </h2>
        {hint && <p className="mt-0.5 text-[13px] text-muted">{hint}</p>}
      </div>
      {action}
    </header>
  );
}

export type Tone = "neutral" | "accent" | "good" | "warn" | "bad" | "info";
const TONES: Record<Tone, string> = {
  neutral: "bg-raised text-muted border-line",
  accent: "bg-accent/10 text-accent-soft border-accent/30",
  good: "bg-good/10 text-good border-good/25",
  warn: "bg-warn/10 text-warn border-warn/25",
  bad: "bg-bad/10 text-bad border-bad/25",
  info: "bg-info/10 text-info border-info/25",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px] font-medium leading-5", TONES[tone], className)}>
      {children}
    </span>
  );
}

// ------------------------------------------------------------------------------------------- Progress
export function Progress({ value, tone = "accent", busy = false, className }: { value: number; tone?: "accent" | "good" | "bad"; busy?: boolean; className?: string }) {
  const colour = tone === "good" ? "bg-good" : tone === "bad" ? "bg-bad" : "bg-accent";
  const percent = Math.min(100, Math.max(0, value * 100));
  return (
    <div className={cx("relative h-1.5 w-full overflow-hidden rounded-full bg-raised", className)} role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
      <div className={cx("h-full rounded-full transition-[width] duration-500 ease-out", colour)} style={{ width: `${percent}%` }} />
      {busy && <div className="busy-sheen pointer-events-none absolute inset-0" />}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("h-4 w-4 animate-spin text-muted", className)} aria-label="Loading" />;
}

// ---------------------------------------------------------------------------------------------- Fields
const FIELD = "w-full rounded-lg border border-line bg-bg px-3 text-sm text-ink placeholder:text-faint transition-colors hover:border-line-strong focus:border-accent focus:outline-none disabled:opacity-50";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(FIELD, "h-9", className)} />;
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(FIELD, "py-2 leading-relaxed", className)} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={cx(FIELD, "h-9 appearance-none bg-[length:16px] bg-[right_10px_center] bg-no-repeat pr-8", className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239ba3b0' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}>
      {children}
    </select>
  );
}

/** A number box that can be typed in freely: the value is applied as soon as it is valid, and what you
 *  are typing is never overwritten mid-keystroke. On blur it shows the value actually in use. */
export function NumberField({ value, onCommit, className, ...rest }: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & { value: number; onCommit: (value: number) => void }) {
  const [text, setText] = useState(String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(String(value));
  }, [value]);
  return (
    <Input
      {...rest} type="number" inputMode="decimal" className={className} value={text}
      onFocus={() => { focused.current = true; }}
      onBlur={() => { focused.current = false; setText(String(value)); }}
      onChange={(event) => {
        setText(event.target.value);
        const parsed = Number(event.target.value);
        if (event.target.value.trim() !== "" && Number.isFinite(parsed)) onCommit(parsed);
      }}
      onKeyDown={(event) => { if (event.key === "Enter") (event.target as HTMLInputElement).blur(); }}
    />
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1.5 block text-[12.5px] font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <label htmlFor={id} className="min-w-0 cursor-pointer">
        <span className="block text-sm text-ink">{label}</span>
        {hint && <span className="block text-xs text-faint">{hint}</span>}
      </label>
      <button
        id={id} type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
        className={cx("relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors duration-200 disabled:opacity-50", checked ? "bg-accent" : "bg-line-strong")}
      >
        <span className={cx("absolute left-0 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-[translate] duration-200", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
      </button>
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options, className }: { value: T; onChange: (value: T) => void; options: { value: T; label: ReactNode; title?: string }[]; className?: string }) {
  return (
    <div className={cx("inline-flex rounded-lg border border-line bg-bg p-0.5", className)} role="tablist">
      {options.map((option) => (
        <button
          key={option.value} type="button" role="tab" title={option.title} aria-selected={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx("rounded-md px-3 py-1.5 text-[13px] transition-colors", option.value === value ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink")}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------------------------------- Modal
export function Modal({ open, onClose, title, children, wide = false, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  // The page behind redraws every second while a job runs, and gives a new onClose each time. Keeping the
  // latest one here lets the effect below run only when the window opens, so it never takes the cursor out of
  // a box you are typing in.
  const closer = useRef(onClose);
  useEffect(() => { closer.current = onClose; }, [onClose]);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && closer.current();
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (!panel.current?.contains(document.activeElement)) panel.current?.focus();  // a box that asked for it keeps it
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div ref={panel} tabIndex={-1} className={cx("relative flex max-h-[92vh] w-full flex-col rounded-2xl border border-line-strong bg-panel shadow-2xl outline-none animate-rise", wide ? "max-w-6xl" : "max-w-xl")}>
        <header className="flex items-center justify-between gap-4 border-b border-line px-5 py-3.5">
          <h2 className="truncate text-base font-semibold">{title}</h2>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X className="h-4 w-4" /></Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <footer className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 py-12 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <p className="text-[15px] font-medium text-ink">{title}</p>
      {children && <div className="mt-1 max-w-md text-[13px] text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  const hint = (error as { hint?: string }).hint;
  return (
    <div role="alert" className={cx("rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-[13px] text-bad", className)}>
      {message}
      {hint && <span className="mt-0.5 block text-bad/80">{hint}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- Toasts
type Toast = { id: number; tone: Tone; title: string; body?: string };
const ToastContext = createContext<(toast: Omit<Toast, "id">) => void>(() => undefined);

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const push = useCallback((toast: Omit<Toast, "id">) => {
    const id = next.current++;
    setToasts((list) => [...list.slice(-3), { ...toast, id }]);
    window.setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), toast.tone === "bad" ? 9000 : 5000);
  }, []);
  const value = useMemo(() => push, [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(92vw,380px)] flex-col gap-2" aria-live="polite">
          {toasts.map((toast) => (
            <div key={toast.id} className={cx("pointer-events-auto rounded-xl border bg-raised px-4 py-3 shadow-xl animate-rise", toast.tone === "bad" ? "border-bad/40" : toast.tone === "good" ? "border-good/40" : "border-line-strong")}>
              <p className="text-sm font-medium text-ink">{toast.title}</p>
              {toast.body && <p className="mt-0.5 break-words text-[13px] text-muted">{toast.body}</p>}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}
