/**
 * Transliate — React primitives (Light mode)
 * Zero dependencies: thin wrappers over the classes in ../transliate.css.
 *
 *   import '../design-pack/transliate.css';
 *   import { Button, Card, Badge, Field, Input } from '../design-pack/react';
 */
import Image from 'next/image';
import React from 'react';
import logo from '../logos/transliate-logo.svg';

const cx = (...c: Array<string | false | null | undefined>) => c.filter(Boolean).join(' ');

// ── Logo ─────────────────────────────────────────────────────
// UIE : avec Next, l'import d'un SVG donne un StaticImageData (pas une URL) → next/image
export function Logo({ height = 40, className, priority }: { height?: number; className?: string; priority?: boolean }) {
  return <Image src={logo} alt="Transliate" priority={priority} style={{ height, width: 'auto' }} className={className} />;
}

// ── Button ───────────────────────────────────────────────────
type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'cta' | 'icon';
type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className, ...rest }, ref) => (
    <button
      ref={ref}
      className={cx('btn', `btn-${variant}`, size !== 'md' && `btn-${size}`, className)}
      {...rest}
    />
  ),
);
Button.displayName = 'Button';

// ── Form ─────────────────────────────────────────────────────
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean; soft?: boolean }>(
  ({ invalid, soft, className, ...rest }, ref) => (
    <input ref={ref} className={cx('input', soft && 'input-soft', invalid && 'input-error', className)} {...rest} />
  ),
);
Input.displayName = 'Input';

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...rest }, ref) => <select ref={ref} className={cx('input', className)} {...rest} />,
);
Select.displayName = 'Select';

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...rest }, ref) => <textarea ref={ref} className={cx('input', className)} {...rest} />,
);
Textarea.displayName = 'Textarea';

export function Field({ label, error, hint, htmlFor, children }: {
  label?: React.ReactNode; error?: React.ReactNode; hint?: React.ReactNode; htmlFor?: string; children: React.ReactNode;
}) {
  return (
    <div>
      {label && <label className="label" htmlFor={htmlFor}>{label}</label>}
      {children}
      {error ? <p className="field-error">{error}</p> : hint ? <p className="field-hint">{hint}</p> : null}
    </div>
  );
}

// ── Badges ───────────────────────────────────────────────────
export function Badge({ color = 'gray', children }: {
  color?: 'blue' | 'green' | 'amber' | 'red' | 'gray' | 'purple'; children: React.ReactNode;
}) {
  return <span className={`badge badge-${color}`}>{children}</span>;
}

export function StatusPill({ variant = 'neutral', dot = true, children }: {
  variant?: 'success' | 'warning' | 'neutral' | 'info'; dot?: boolean; children: React.ReactNode;
}) {
  return <span className={cx('pill', `pill-${variant}`, dot && 'pill-dot')}>{children}</span>;
}

// ── Surfaces ─────────────────────────────────────────────────
export function Card({ panel, className, ...rest }: React.HTMLAttributes<HTMLDivElement> & { panel?: boolean }) {
  return <div className={cx(panel ? 'panel' : 'card', className)} {...rest} />;
}

export function KpiCard({ label, value, sub }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="kpi-card">
      <p className="kpi-label">{label}</p>
      <p className="kpi-value">{value}</p>
      {sub && <p className="kpi-sub">{sub}</p>}
    </div>
  );
}

export function StatCard({ label, value, icon, progress = 66 }: {
  label: React.ReactNode; value: React.ReactNode; icon?: React.ReactNode; progress?: number;
}) {
  return (
    <div className="stat-card">
      <div className="stat-card__head">
        <div>
          <p className="stat-card__label">{label}</p>
          <p className="stat-card__value">{value}</p>
        </div>
        {icon && <div className="stat-card__icon">{icon}</div>}
      </div>
      <div className="stat-card__bar"><span style={{ width: `${progress}%` }} /></div>
    </div>
  );
}

// ── Modal ────────────────────────────────────────────────────
export function Modal({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
        {title && <div style={{ padding: '1.25rem 1.5rem', borderBottom: '1px solid var(--border-lt)' }}><h2 className="h-section">{title}</h2></div>}
        <div style={{ padding: '1.5rem', overflowY: 'auto' }}>{children}</div>
        {footer && <div style={{ padding: '1rem 1.5rem', borderTop: '1px solid var(--border-lt)', display: 'flex', justifyContent: 'flex-end', gap: '.5rem' }}>{footer}</div>}
      </div>
    </div>
  );
}
