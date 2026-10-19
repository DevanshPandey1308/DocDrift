import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getRepositories, getRuns } from "../api/client";
import { Breadcrumbs, EmptyState, ErrorPanel, LoadingState, PageHeader, StatusBadge } from "../components/ui";
import { formatDate } from "./Overview";

export function Repositories() {
  const repositories = useQuery({ queryKey: ["repositories"], queryFn: getRepositories });
  return (
    <>
      <PageHeader eyebrow="Your workspace" title="Repositories" description="Repositories authorized for your GitHub account and the DocDrift installation." />
      {repositories.isPending ? <LoadingState label="Loading repositories…" />
        : repositories.isError ? <ErrorPanel error={repositories.error} />
          : repositories.data.length ? (
            <ul className="repository-grid">
              {repositories.data.map((repository) => (
                <li key={repository.id}><Link className="repository-tile" to={`/repositories/${repository.id}`}>
                  <span className="repo-marker" aria-hidden="true">R</span>
                  <span className="tile-name">{repository.full_name}</span>
                  <span className="tile-description">Review analysis runs</span>
                  <span className="inline-link">Open repository →</span>
                </Link></li>
              ))}
            </ul>
          ) : <EmptyState title="No repositories available" description="No repositories are both installed for DocDrift and accessible to this GitHub account." />}
    </>
  );
}

export function RepositoryPage() {
  const { repositoryId } = useParams();
  const id = Number(repositoryId);
  const repositories = useQuery({ queryKey: ["repositories"], queryFn: getRepositories });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => getRuns(id), enabled: Number.isInteger(id) && id > 0 });
  if (repositories.isPending || runs.isPending) return <LoadingState label="Loading repository activity…" />;
  if (repositories.isError) return <ErrorPanel error={repositories.error} />;
  if (runs.isError) return <ErrorPanel error={runs.error} />;
  const repository = repositories.data.find((item) => item.id === id);
  if (!repository) return <EmptyState title="Repository unavailable" description="It may have been removed or your GitHub access may have changed." />;

  return (
    <>
      <Breadcrumbs items={[{ label: "Repositories", to: "/repositories" }, { label: repository.full_name }]} />
      <PageHeader eyebrow="Repository" title={repository.full_name} description="Pull request analyses and their current status." />
      <section className="surface-panel table-panel" aria-labelledby="run-table-title">
        <div className="panel-header"><div><p className="eyebrow">Analysis history</p><h2 id="run-table-title">Pull request runs</h2></div></div>
        {runs.data.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th scope="col">Pull request</th><th scope="col">Status</th><th scope="col">Findings</th><th scope="col">Created</th><th scope="col"><span className="sr-only">Open run</span></th></tr></thead>
              <tbody>{runs.data.map((run) => (
                <tr key={run.id}>
                  <td><Link className="table-link" to={`/runs/${run.id}`}>#{run.pull_request_number}</Link><small className="sha-value">{run.head_sha.slice(0, 8)}</small></td>
                  <td><StatusBadge value={run.status} /></td>
                  <td><span className="count-value">{run.findings_count}</span><small className="table-secondary">{run.false_count} false · {run.unverifiable_count} unverifiable</small></td>
                  <td>{formatDate(run.created_at)}</td>
                  <td><Link className="table-link" aria-label={`View pull request ${run.pull_request_number}`} to={`/runs/${run.id}`}>View</Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <EmptyState title="No pull request runs" description="Analysis runs will appear here after a pull request is received." />}
      </section>
    </>
  );
}
