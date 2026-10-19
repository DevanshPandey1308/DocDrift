import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, signIn } from "../api/client";

type ButtonVariant = "primary" | "secondary" | "quiet";

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={`button button-${variant} ${className}`.trim()} {...props} />;
}

export function PageHeader({ eyebrow, title, description }: {
  eyebrow: string;
  title: ReactNode;
  description: string;
}) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="page-description">{description}</p>
    </header>
  );
}

export function Breadcrumbs({ items }: { items: Array<{ label: string; to?: string }> }) {
  return (
    <nav className="breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`}>
            {index > 0 && <span className="breadcrumb-separator" aria-hidden="true">/</span>}
            {item.to ? <Link to={item.to}>{item.label}</Link> : <span aria-current="page">{item.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StatusBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll("_", " ");
  const tone = value.toLowerCase().replace(/[^a-z]/g, "-");
  return <span className={`status-badge status-${tone}`}><span className="status-dot" aria-hidden="true" />{normalized}</span>;
}

export function MetricCard({ label, value, detail }: {
  label: string;
  value: number | string;
  detail: string;
}) {
  return (
    <article className="metric-card">
      <p>{label}</p>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

export function MetricStrip({ label, values }: {
  label: string;
  values: Array<{ label: string; value: number | string }>;
}) {
  return (
    <dl className="metric-strip" aria-label={label}>
      {values.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function LoadingState({ label = "Loading workspace…" }: { label?: string }) {
  return <div className="loading-state" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function EmptyState({ title, description, action }: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorPanel({ error }: { error: Error }) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) {
    return (
      <section className="error-panel" role="alert">
        <h2>Sign in to view this workspace</h2>
        <p>DocDrift checks your GitHub account and repository access before showing data.</p>
        <Button variant="primary" onClick={signIn}>Continue with GitHub</Button>
      </section>
    );
  }
  const message = status === 403
    ? "Access denied. Check your GitHub repository access and try again."
    : status === 404
      ? "This repository or run is no longer available to your account."
      : error.message || "Could not load dashboard data.";
  return (
    <section className="error-panel" role="alert">
      <h2>{status === 403 ? "Access unavailable" : status === 404 ? "Resource unavailable" : "Could not load data"}</h2>
      <p>{message}</p>
      <Button onClick={() => window.location.reload()}>Try again</Button>
    </section>
  );
}

export function FeedbackMessage({ success, error }: { success?: string; error?: string }) {
  if (error) return <p className="feedback-message feedback-error" role="alert">{error}</p>;
  if (success) return <p className="feedback-message feedback-success" role="status">{success}</p>;
  return null;
}
