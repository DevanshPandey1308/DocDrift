import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMetrics, getRepositories, getRuns } from "../api/client";
import { EmptyState, ErrorPanel, LoadingState, MetricCard, MetricStrip, PageHeader, StatusBadge } from "../components/ui";
import { formatDate } from "../utils/date";

export function Overview() {
  const repositories = useQuery({ queryKey: ["repositories"], queryFn: getRepositories });
  const repoIds = repositories.data?.map((repo) => repo.id) ?? [];
  const overview = useQuery({
    queryKey: ["overview", repoIds],
    enabled: repositories.isSuccess && repoIds.length > 0,
    queryFn: async () => Promise.all(repositories.data!.map(async (repository) => ({
      repository,
      metrics: await getMetrics(repository.id),
      runs: await getRuns(repository.id),
    }))),
  });

  if (repositories.isPending) return <><Header /><LoadingState label="Loading authorized repositories…" /></>;
  if (repositories.isError) return <><Header /><ErrorPanel error={repositories.error} /></>;
  if (repoIds.length > 0 && overview.isPending) return <><Header /><LoadingState label="Loading repository activity…" /></>;
  if (overview.isError) return <><Header /><ErrorPanel error={overview.error} /></>;

  const entries = overview.data ?? [];
  const runs = entries.flatMap(({ repository, runs: repoRuns }) =>
    repoRuns.map((run) => ({ ...run, repositoryName: repository.full_name })),
  ).sort((left, right) => right.created_at.localeCompare(left.created_at));
  const accepted = entries.reduce((total, item) => total + item.metrics.accepted_fixes, 0);
  const rejected = entries.reduce((total, item) => total + item.metrics.rejected_fixes, 0);
  const falsePositives = entries.reduce((total, item) => total + item.metrics.false_positives, 0);
  const outcomeCount = accepted + rejected;
  const acceptanceRate = outcomeCount ? `${Math.round((accepted / outcomeCount) * 100)}%` : "—";

  return (
    <>
      <Header />
      <section className="metric-grid" aria-label="Workspace summary">
        <MetricCard label="Authorized repositories" value={repositories.data.length} detail="Accessible through GitHub" />
        <MetricCard label="Recent pull request runs" value={runs.length} detail="Most recent runs returned by the API" />
        <MetricCard label="Fix acceptance" value={acceptanceRate} detail={outcomeCount ? `${accepted} accepted of ${outcomeCount} outcomes` : "No accepted or rejected feedback yet"} />
      </section>
      <MetricStrip label="Feedback outcomes" values={[
        { label: "Accepted", value: accepted },
        { label: "Rejected", value: rejected },
        { label: "False positives", value: falsePositives },
        { label: "Rate denominator", value: outcomeCount },
      ]} />

      <div className="overview-grid">
        <section className="surface-panel" aria-labelledby="overview-repositories-title">
          <div className="panel-header"><div><p className="eyebrow">Connected</p><h2 id="overview-repositories-title">Repositories</h2></div><Link className="inline-link" to="/repositories">View all</Link></div>
          {repositories.data.length ? (
            <ul className="repository-list">
              {repositories.data.slice(0, 6).map((repository) => (
                <li key={repository.id}><Link to={`/repositories/${repository.id}`}>
                  <span className="repo-marker" aria-hidden="true">R</span>
                  <span className="repository-name">{repository.full_name}<small>Open pull request runs</small></span>
                  <span className="row-action" aria-hidden="true">→</span>
                </Link></li>
              ))}
            </ul>
          ) : <EmptyState title="No authorized repositories" description="Install the DocDrift GitHub App and confirm your account can access a repository." />}
        </section>

        <section className="surface-panel" aria-labelledby="overview-runs-title">
          <div className="panel-header"><div><p className="eyebrow">Recent activity</p><h2 id="overview-runs-title">Pull request runs</h2></div></div>
          {runs.length ? (
            <ul className="run-list">
              {runs.slice(0, 7).map((run) => (
                <li key={run.id}><Link to={`/runs/${run.id}`}>
                  <span className="run-description"><strong>{run.repositoryName} <span>#{run.pull_request_number}</span></strong><small>{formatDate(run.created_at)} · {run.findings_count} findings</small></span>
                  <StatusBadge value={run.status} />
                </Link></li>
              ))}
            </ul>
          ) : <EmptyState title="No pull request runs" description="Analysis runs will appear here after a pull request is received." />}
        </section>
      </div>
    </>
  );
}

function Header() {
  return <PageHeader eyebrow="Workspace" title="Overview" description="Repository checks and explicit maintainer feedback." />;
}
