'use client';

// frontend/app/components/AuthFormBits.jsx
// Small shared building blocks for the sign-in / sign-up forms.
import React, { useState } from 'react';
import { Eye, EyeOff, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';

/* Google's mark, inlined — the artifact CSP blocks remote images, and this
   keeps the button working offline. */
export const GoogleMark = (props) => (
  <svg viewBox="0 0 18 18" className="w-4 h-4" aria-hidden="true" {...props}>
    <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z" />
    <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z" />
    <path fill="#FBBC05" d="M3.97 10.71a5.41 5.41 0 0 1 0-3.42V4.96H.96a9 9 0 0 0 0 8.08l3.01-2.33z" />
    <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z" />
  </svg>
);

export const GoogleButton = ({ onClick, disabled, label }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="w-full flex items-center justify-center gap-2.5 px-3.5 py-2.5 rounded-lg border-2 border-field bg-ground text-ink font-semibold text-[14px] hover:border-field-hover hover:bg-surface transition-colors duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
  >
    <GoogleMark />
    <span>{label}</span>
  </button>
);

export const Divider = ({ children }) => (
  <div className="flex items-center gap-3" role="separator">
    <span className="h-px flex-1 bg-[color:var(--line)]" />
    <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
      {children}
    </span>
    <span className="h-px flex-1 bg-[color:var(--line)]" />
  </div>
);

export const Field = ({
  id, label, type = 'text', value, onChange, placeholder,
  autoComplete, error, hint, children, ...rest
}) => (
  <div className="flex flex-col gap-1.5">
    <div className="flex items-center justify-between gap-3">
      <label htmlFor={id} className="text-[13px] font-bold text-ink">
        {label}
      </label>
      {children}
    </div>
    <input
      id={id}
      name={id}
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      autoComplete={autoComplete}
      aria-invalid={error ? 'true' : undefined}
      aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
      className={`w-full rounded-lg bg-ground border-2 px-3.5 py-2.5 text-[14px] text-ink placeholder:text-ink-faint transition-colors duration-150 focus:outline-none focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-soft)] ${
        error ? 'border-[color:var(--accent)]' : 'border-field hover:border-field-hover'
      }`}
      {...rest}
    />
    {error ? (
      <p id={`${id}-error`} className="text-[12px] font-bold text-accent flex items-center gap-1.5">
        <AlertCircle className="w-3.5 h-3.5 shrink-0" />
        {error}
      </p>
    ) : hint ? (
      <p id={`${id}-hint`} className="text-[12px] text-ink-faint font-medium">{hint}</p>
    ) : null}
  </div>
);

/** Password field with a show/hide toggle that keeps the label row tidy. */
export const PasswordField = ({ id, label, value, onChange, error, hint, autoComplete, children }) => {
  const [show, setShow] = useState(false);
  return (
    <Field
      id={id}
      label={label}
      type={show ? 'text' : 'password'}
      value={value}
      onChange={onChange}
      placeholder="••••••••"
      autoComplete={autoComplete}
      error={error}
      hint={hint}
    >
      <div className="flex items-center gap-3">
        {children}
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="text-[12px] font-bold text-ink-muted hover:text-accent transition-colors flex items-center gap-1"
          aria-pressed={show}
        >
          {show ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          {show ? 'Hide' : 'Show'}
        </button>
      </div>
    </Field>
  );
};

/** Inline banner for form-level success / error / info messages. */
export const Banner = ({ kind = 'error', children }) => {
  if (!children) return null;
  const Icon = kind === 'success' ? CheckCircle2 : AlertCircle;
  const tone =
    kind === 'success'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
      : 'border-[color:var(--accent)] bg-accent-soft text-accent';
  return (
    <div role="alert" className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-[13px] font-bold ${tone}`}>
      <Icon className="w-4 h-4 shrink-0 mt-0.5" />
      <span className="leading-relaxed">{children}</span>
    </div>
  );
};

export const SubmitButton = ({ busy, children }) => (
  <button
    type="submit"
    disabled={busy}
    className="w-full flex items-center justify-center gap-2 px-3.5 py-2.5 rounded-lg bg-accent text-white font-bold text-[14px] hover:brightness-110 transition-[filter,opacity] duration-150 disabled:opacity-60 disabled:cursor-not-allowed"
  >
    {busy && <Loader2 className="w-4 h-4 animate-spin" />}
    {children}
  </button>
);
