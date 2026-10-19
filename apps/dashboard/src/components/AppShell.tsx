import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { Button } from "./ui";
import { signIn } from "../api/client";

export function AppShell({
  login,
  onSignOut,
  signOutPending,
  signOutError,
  children,
}: {
  login: string;
  onSignOut: () => void;
  signOutPending: boolean;
  signOutError?: string;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/" aria-label="DocDrift overview">
          <span className="brand-mark" aria-hidden="true">D</span>
          <span className="brand-copy">DocDrift<small>Documentation intelligence</small></span>
        </Link>
        <div className="sidebar-navigation">
          <p className="nav-caption">Workspace</p>
          <nav aria-label="Main navigation">
            <NavLink end to="/">Overview</NavLink>
            <NavLink to="/repositories">Repositories</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </div>
        <div className="account-panel">
          <span className="account-avatar" aria-hidden="true">{login.slice(0, 1).toUpperCase()}</span>
          <span className="account-copy">{login}<small>GitHub account</small></span>
          <Button variant="quiet" className="sign-out" disabled={signOutPending} onClick={onSignOut}>
            {signOutPending ? "Signing out…" : "Sign out"}
          </Button>
          {signOutError && <p className="account-error" role="alert">{signOutError}</p>}
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  );
}

export function SignInPage() {
  return (
    <main className="sign-in-page">
      <section className="sign-in-panel" aria-labelledby="sign-in-title">
        <span className="brand-mark sign-in-mark" aria-hidden="true">D</span>
        <p className="eyebrow">DocDrift dashboard</p>
        <h1 id="sign-in-title">Documentation that stays aligned.</h1>
        <p>Review repository checks, inspect source evidence, and give feedback on verified suggestions.</p>
        <Button variant="primary" className="sign-in-button" onClick={signIn}>
          Continue with GitHub
        </Button>
        <small>Authentication is handled by the DocDrift API. Session tokens are not stored in this browser.</small>
      </section>
    </main>
  );
}
