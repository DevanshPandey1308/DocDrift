import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { Link, NavLink, Route, Routes, useParams } from "react-router-dom";
import { ApiError, getMetrics, getRepositories, getRun, getRuns, getSession, logout, signIn, submitFeedback } from "./api/client";

function Failure({ error }: { error: Error }) {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) return <section className="empty"><h2>Sign in to view your workspace</h2><p>DocDrift uses GitHub OAuth and checks repository access with your account.</p><button onClick={signIn}>Continue with GitHub</button></section>;
  if (status === 403) return <section role="alert" className="notice">Access denied. Check your GitHub repository access and try again.</section>;
  if (status === 404) return <section role="alert" className="notice">This repository or run is no longer available to your account.</section>;
  return <section role="alert" className="notice">{error.message || "Could not load dashboard data."} <button className="text-button" onClick={() => window.location.reload()}>Try again</button></section>;
}

function Loading({ label = "Loading workspace…" }: { label?: string }) { return <div className="loading" role="status"><span className="spinner" />{label}</div>; }
function Empty({ title, text }: { title: string; text: string }) { return <div className="empty"><h2>{title}</h2><p>{text}</p></div>; }

export default function App() {
  const session = useQuery({ queryKey: ["session"], queryFn: getSession, retry: false });
  const qc = useQueryClient();
  const signOut = useMutation({ mutationFn: logout, onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["session"] }); } });
  if (session.isPending) return <div className="center-page"><Loading label="Checking GitHub session…" /></div>;
  if (session.isError && session.error instanceof ApiError && session.error.status === 401) return <div className="center-page signin"><div className="brand-mark">D</div><p className="eyebrow">DOC DRIFT</p><h1>Keep documentation<br />in sync with code.</h1><p>Review deterministic checks, inspect source evidence, and share feedback on verified suggestions.</p><button onClick={signIn}>Sign in with GitHub <span aria-hidden="true">↗</span></button><small>Authentication is handled by the DocDrift API. No token is stored in this browser.</small></div>;
  if (session.isError) return <div className="center-page"><Failure error={session.error} /></div>;
  return <div className="shell">
    <aside className="sidebar"><Link className="brand" to="/"><span className="brand-mark small">D</span><span>docdrift<small>Documentation intelligence</small></span></Link>
      <p className="nav-caption">WORKSPACE</p><nav aria-label="Main navigation"><NavLink end to="/">Overview</NavLink><NavLink to="/repositories">Repositories</NavLink><NavLink to="/settings">Settings</NavLink></nav>
      <div className="sidebar-foot"><div className="user-avatar" aria-hidden="true">{session.data.github_login.slice(0, 1).toUpperCase()}</div><span className="user-name">{session.data.github_login}<small>GitHub account</small></span><button className="icon-button" aria-label="Sign out" title="Sign out" disabled={signOut.isPending} onClick={() => signOut.mutate()}>↗</button></div>
    </aside>
    <main className="main-content"><Routes><Route path="/" element={<Overview />} /><Route path="/repositories" element={<Repositories />} /><Route path="/repositories/:repositoryId" element={<RepositoryPage />} /><Route path="/runs/:runId" element={<RunPage />} /><Route path="/settings" element={<SettingsPage />} /><Route path="*" element={<Empty title="Page not found" text="The requested dashboard page does not exist." />} /></Routes></main>
  </div>;
}

function useRepos() { return useQuery({ queryKey: ["repositories"], queryFn: getRepositories }); }
function useRepoRuns(id: number) { return useQuery({ queryKey: ["runs", id], queryFn: () => getRuns(id), enabled: Number.isInteger(id) && id > 0 }); }

function Overview() {
  const repos = useRepos();
  const metricsQueries = useQuery({ queryKey: ["overview-metrics", repos.data?.map((r) => r.id)], enabled: !!repos.data?.length,
    queryFn: async () => Promise.all(repos.data!.map(async (repo) => ({ repo, metrics: await getMetrics(repo.id), runs: await getRuns(repo.id) }))) });
  if (repos.isPending || metricsQueries.isPending) return <><PageHeading eyebrow="WORKSPACE" title="Overview" text="A clear view of repository health and maintainer feedback." /><Loading /></>;
  if (repos.isError) return <><PageHeading eyebrow="WORKSPACE" title="Overview" text="A clear view of repository health and maintainer feedback." /><Failure error={repos.error} /></>;
  if (metricsQueries.isError) return <><PageHeading eyebrow="WORKSPACE" title="Overview" text="A clear view of repository health and maintainer feedback." /><Failure error={metricsQueries.error} /></>;
  const allRuns = metricsQueries.data?.flatMap((item) => item.runs.map((run) => ({ ...run, repository: item.repo.full_name }))) ?? [];
  allRuns.sort((a, b) => b.created_at.localeCompare(a.created_at));
  const metrics = metricsQueries.data?.map((x) => x.metrics) ?? [];
  const accepted = metrics.reduce((n, x) => n + x.accepted_fixes, 0); const rejected = metrics.reduce((n, x) => n + x.rejected_fixes, 0);
  const falsePositives = metrics.reduce((n, x) => n + x.false_positives, 0);
  const total = accepted + rejected;
  return <><PageHeading eyebrow="WORKSPACE" title="Overview" text="A clear view of repository health and maintainer feedback." />
    <div className="stat-grid"><Stat label="Repositories" value={repos.data.length} detail="authorized on GitHub" /><Stat label="Pull request runs" value={allRuns.length} detail="latest available runs" /><Stat label="Fix acceptance" value={total ? `${Math.round(accepted / total * 100)}%` : "—"} detail={total ? `${accepted} accepted of ${total} outcomes` : "No accepted or rejected feedback yet"} /></div>
    <div className="metrics-strip" aria-label="Feedback acceptance metrics"><span>Accepted <strong>{accepted}</strong></span><span>Rejected <strong>{rejected}</strong></span><span>False positives <strong>{falsePositives}</strong></span><span>Outcome denominator <strong>{total}</strong></span></div>
    <div className="content-grid"><section className="panel"><div className="panel-heading"><div><p className="eyebrow">CONNECTED</p><h2>Repositories</h2></div><Link className="quiet-link" to="/repositories">View all →</Link></div>
      {repos.data.length ? <ul className="repo-list">{repos.data.slice(0, 6).map((repo) => <li key={repo.id}><Link to={`/repositories/${repo.id}`}><span className="repo-icon">⌘</span><span>{repo.full_name}<small>View pull request runs</small></span><span className="row-arrow">→</span></Link></li>)}</ul> : <Empty title="No authorized repositories" text="Install the DocDrift GitHub App and confirm your GitHub account can access those repositories." />}</section>
      <section className="panel"><div className="panel-heading"><div><p className="eyebrow">RECENT ACTIVITY</p><h2>Pull request runs</h2></div></div>
        {allRuns.length ? <ul className="run-list">{allRuns.slice(0, 7).map((run) => <li key={run.id}><Link to={`/runs/${run.id}`}><div><strong>{run.repository} <span className="muted">#{run.pull_request_number}</span></strong><small>{date(run.created_at)} · {run.findings_count} findings</small></div><Status value={run.status} /></Link></li>)}</ul> : <Empty title="No runs yet" text="New pull request analyses will appear here." />}</section></div>
  </>;
}
function PageHeading({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) { return <header className="page-heading"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{text}</p></header>; }
function Stat({ label, value, detail }: { label: string; value: number | string; detail: string }) { return <div className="stat-card"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>; }
function Status({ value }: { value: string }) { return <span className={`status status-${value.replace(/[^a-z]/gi, "-")}`}>{value.replaceAll("_", " ")}</span>; }
function date(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }

function Repositories() {
  const repos = useRepos();
  return <><PageHeading eyebrow="YOUR WORKSPACE" title="Repositories" text="Repositories you can access through the DocDrift installation." />
    {repos.isPending ? <Loading /> : repos.isError ? <Failure error={repos.error} /> : repos.data.length ? <div className="repository-grid">{repos.data.map((repo) => <Link className="repository-card" key={repo.id} to={`/repositories/${repo.id}`}><span className="repo-icon">⌘</span><h2>{repo.full_name}</h2><p>Open pull request runs and findings</p><span className="quiet-link">Explore →</span></Link>)}</div> : <Empty title="No repositories available" text="No repositories are both installed for DocDrift and accessible to this GitHub account." />}</>;
}

function RepositoryPage() {
  const { repositoryId } = useParams(); const id = Number(repositoryId); const repos = useRepos(); const runs = useRepoRuns(id);
  if (repos.isPending || runs.isPending) return <Loading />;
  if (repos.isError) return <Failure error={repos.error} />; if (runs.isError) return <Failure error={runs.error} />;
  const repo = repos.data.find((item) => item.id === id); if (!repo) return <Empty title="Repository unavailable" text="It may have been removed or your GitHub access may have changed." />;
  return <><div className="breadcrumbs"><Link to="/repositories">Repositories</Link><span>/</span>{repo.full_name}</div><PageHeading eyebrow="REPOSITORY" title={repo.full_name} text="Pull request analyses and their current status." />
    <section className="panel table-panel"><div className="panel-heading"><div><p className="eyebrow">ANALYSIS HISTORY</p><h2>Pull request runs</h2></div></div>
      {runs.data.length ? <div className="table-wrap"><table><thead><tr><th>Pull request</th><th>Status</th><th>Findings</th><th>Created</th><th /></tr></thead><tbody>{runs.data.map((run) => <tr key={run.id}><td><Link to={`/runs/${run.id}`}>#{run.pull_request_number}</Link><small className="sha">{run.head_sha.slice(0, 8)}</small></td><td><Status value={run.status} /></td><td>{run.findings_count} <small className="muted">({run.false_count} false · {run.unverifiable_count} unverifiable)</small></td><td>{date(run.created_at)}</td><td><Link aria-label={`View pull request ${run.pull_request_number}`} to={`/runs/${run.id}`}>→</Link></td></tr>)}</tbody></table></div> : <Empty title="No pull request runs" text="Analysis runs will appear when a pull request is received." />}</section></>;
}

function RunPage() {
  const { runId } = useParams(); const id = Number(runId); const queryClient = useQueryClient();
  const retryKeys = useRef(new Map<string, string>());
  const run = useQuery({ queryKey: ["run", id], queryFn: () => getRun(id), enabled: Number.isInteger(id) && id > 0 });
  const metrics = useQuery({ queryKey: ["metrics", run.data?.repository_id], queryFn: () => getMetrics(run.data!.repository_id), enabled: !!run.data });
  const feedback = useMutation({ mutationFn: ({ target, findingId, category }: { target: "suggestions" | "findings"; findingId: number; category: "fix_accepted" | "fix_rejected" | "false_positive" }) => {
    const key = `${target}:${findingId}:${category}`;
    const idempotencyKey = retryKeys.current.get(key) ?? crypto.randomUUID();
    retryKeys.current.set(key, idempotencyKey);
    return submitFeedback(target, findingId, category, idempotencyKey);
  }, onSuccess: async (_result, variables) => {
    retryKeys.current.delete(`${variables.target}:${variables.findingId}:${variables.category}`);
    await Promise.all([queryClient.invalidateQueries({ queryKey: ["run", id] }), queryClient.invalidateQueries({ queryKey: ["metrics"] }), queryClient.invalidateQueries({ queryKey: ["overview-metrics"] })]);
  } });
  if (run.isPending) return <Loading />; if (run.isError) return <Failure error={run.error} />;
  const data = run.data;
  return <><div className="breadcrumbs"><Link to={`/repositories/${data.repository_id}`}>Repository runs</Link><span>/</span>#{data.pull_request_number}</div>
    <PageHeading eyebrow="PULL REQUEST ANALYSIS" title={`Pull request #${data.pull_request_number}`} text={`Head ${data.head_sha.slice(0, 12)} · ${date(data.created_at)}`} />
    <div className="stat-grid compact"><Stat label="Run status" value={data.status.replaceAll("_", " ")} detail={`Publication: ${data.publication_status.replaceAll("_", " ")}`} /><Stat label="Findings" value={data.findings_count} detail={`${data.false_count} false · ${data.unverifiable_count} unverifiable`} /><Stat label="Acceptance rate" value={metrics.data?.acceptance_rate == null ? "—" : `${Math.round(metrics.data.acceptance_rate * 100)}%`} detail={metrics.isPending ? "Loading feedback metrics" : metrics.data ? `${metrics.data.numerator} / ${metrics.data.denominator} accepted outcomes` : "Metrics unavailable"} /></div>
    {metrics.isError && <Failure error={metrics.error} />}
    {metrics.data && <div className="metrics-strip" aria-label="Repository feedback metrics"><span>Accepted <strong>{metrics.data.accepted_fixes}</strong></span><span>Rejected <strong>{metrics.data.rejected_fixes}</strong></span><span>False positives <strong>{metrics.data.false_positives}</strong></span><span>Numerator / denominator <strong>{metrics.data.numerator} / {metrics.data.denominator}</strong></span></div>}
    <section className="finding-section"><div className="section-title"><div><p className="eyebrow">REVIEW</p><h2>Findings and evidence</h2></div><span className="muted">{data.findings.length} total</span></div>
      {data.findings.length ? data.findings.map((finding) => <article key={finding.id} className={`finding-card ${finding.ignored ? "finding-ignored" : ""}`}><header className="finding-header"><div><Status value={finding.ignored ? "ignored" : finding.verdict} /><span className="claim-type">{finding.claim_type.replaceAll("_", " ")}</span></div><a className="file-location" href={`#finding-${finding.id}`} id={`finding-${finding.id}`}>{finding.document_path}:{finding.document_line}</a></header>
        <div className="finding-body"><p className="claim-label">DOCUMENTATION CLAIM</p><blockquote>{finding.claim_text}</blockquote><p>{finding.explanation}</p>{finding.ignored && finding.ignore_reason && <p className="muted">Ignored: {finding.ignore_reason}</p>}
          {finding.evidence.length > 0 && <section className="evidence"><h3>Supporting source evidence</h3>{finding.evidence.map((item, index) => <div className="evidence-item" key={`${item.path}:${item.line}:${index}`}><span>{item.path}:{item.line}</span><pre><code>{item.snippet}</code></pre></div>)}</section>}
          {finding.suggestions.filter((suggestion) => suggestion.publication_status === "published").map((suggestion) => <div className="suggestion" key={suggestion.id}><div><strong>Verified suggestion published</strong><p>{suggestion.document_path}:{suggestion.document_line} · head {suggestion.head_sha.slice(0, 8)}</p><small>The stored record does not contain the proposed diff.</small></div><div className="feedback-buttons"><button disabled={feedback.isPending} onClick={() => feedback.mutate({ target: "suggestions", findingId: suggestion.id, category: "fix_accepted" })}>Accept</button><button className="secondary" disabled={feedback.isPending} onClick={() => feedback.mutate({ target: "suggestions", findingId: suggestion.id, category: "fix_rejected" })}>Reject</button></div></div>)}
          {!finding.ignored && <button className="text-button" disabled={feedback.isPending} onClick={() => feedback.mutate({ target: "findings", findingId: finding.id, category: "false_positive" })}>Report false positive</button>}
          {feedback.isError && <p role="alert" className="feedback-error">{feedback.error.message} Retry the same action to safely reuse its idempotency key.</p>}
          {feedback.isSuccess && <p role="status" className="feedback-success">Feedback recorded: {feedback.data.category.replaceAll("_", " ")}.</p>}
        </div></article>) : <Empty title="No findings" text="This run has no stored findings to review." />}</section>
  </>;
}
function SettingsPage() { return <><PageHeading eyebrow="PREFERENCES" title="Settings" text="Dashboard preferences and repository configuration." /><section className="panel"><Empty title="Settings are managed in your repository" text="Finding exclusions and severity controls are configured in .docdrift.yml. Dashboard preferences are not available yet." /></section></>; }
