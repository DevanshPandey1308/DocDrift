import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useParams } from "react-router-dom";
import { getMetrics, getRun, submitFeedback, type FeedbackCategory } from "../api/client";
import { FindingCard } from "../components/FindingCard";
import { Breadcrumbs, EmptyState, ErrorPanel, LoadingState, MetricCard, MetricStrip, PageHeader } from "../components/ui";
import { formatDate } from "../utils/date";

export function RunPage() {
  const { runId } = useParams();
  const id = Number(runId);
  const queryClient = useQueryClient();
  const retryKeys = useRef(new Map<string, ReturnType<typeof crypto.randomUUID>>());
  const run = useQuery({ queryKey: ["run", id], queryFn: () => getRun(id), enabled: Number.isInteger(id) && id > 0 });
  const metrics = useQuery({
    queryKey: ["metrics", run.data?.repository_id],
    queryFn: () => getMetrics(run.data!.repository_id),
    enabled: !!run.data,
  });
  const feedback = useMutation({
    mutationFn: ({ target, recordId, category }: { target: "suggestions" | "findings"; recordId: number; category: FeedbackCategory }) => {
      const retryKey = `${target}:${recordId}:${category}`;
      const idempotencyKey = retryKeys.current.get(retryKey) ?? crypto.randomUUID();
      retryKeys.current.set(retryKey, idempotencyKey);
      return submitFeedback(target, recordId, category, idempotencyKey);
    },
    onSuccess: async (_result, variables) => {
      retryKeys.current.delete(`${variables.target}:${variables.recordId}:${variables.category}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["run", id] }),
        queryClient.invalidateQueries({ queryKey: ["metrics"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ]);
    },
  });

  if (run.isPending) return <LoadingState label="Loading pull request findings…" />;
  if (run.isError) return <ErrorPanel error={run.error} />;
  const data = run.data;
  const acceptedRate = metrics.data?.acceptance_rate == null
    ? "—"
    : `${Math.round(metrics.data.acceptance_rate * 100)}%`;

  return (
    <>
      <Breadcrumbs items={[{ label: "Repositories", to: "/repositories" }, { label: `Pull request #${data.pull_request_number}` }]} />
      <PageHeader eyebrow="Pull request analysis" title={`Pull request #${data.pull_request_number}`} description={`Head ${data.head_sha} · ${formatDate(data.created_at)}`} />
      <section className="metric-grid run-metrics" aria-label="Run summary">
        <MetricCard label="Run status" value={data.status.replaceAll("_", " ")} detail={`Publication: ${data.publication_status.replaceAll("_", " ")}`} />
        <MetricCard label="Active findings" value={data.findings_count} detail={`${data.false_count} false · ${data.unverifiable_count} unverifiable`} />
        <MetricCard label="Acceptance rate" value={acceptedRate} detail={metrics.isPending ? "Loading feedback outcomes" : metrics.data ? `${metrics.data.numerator} accepted / ${metrics.data.denominator} outcomes` : "Feedback metrics unavailable"} />
      </section>
      {metrics.isError && <ErrorPanel error={metrics.error} />}
      {metrics.data && <MetricStrip label="Repository feedback outcomes" values={[
        { label: "Accepted", value: metrics.data.accepted_fixes },
        { label: "Rejected", value: metrics.data.rejected_fixes },
        { label: "False positives", value: metrics.data.false_positives },
        { label: "Numerator / denominator", value: `${metrics.data.numerator} / ${metrics.data.denominator}` },
      ]} />}

      <section className="findings-section" aria-labelledby="findings-title">
        <div className="section-heading"><div><p className="eyebrow">Review</p><h2 id="findings-title">Findings and evidence</h2></div><span>{data.findings.length} records</span></div>
        {data.findings.length ? data.findings.map((finding) => {
          const variables = feedback.variables;
          const matchesFinding = variables?.target === "findings" && variables.recordId === finding.id;
          const matchesSuggestion = variables?.target === "suggestions" && finding.suggestions.some((item) => item.id === variables.recordId);
          const matches = matchesFinding || matchesSuggestion;
          const error = matches && feedback.isError
            ? `${feedback.error.message} Retry this action to safely reuse its idempotency key.`
            : undefined;
          const success = matches && feedback.isSuccess
            ? `Feedback recorded: ${feedback.data.category.replaceAll("_", " ")}.`
            : undefined;
          return <FindingCard
            key={finding.id}
            finding={finding}
            pending={feedback.isPending}
            feedbackError={error}
            feedbackSuccess={success}
            onFeedback={(target, recordId, category) => feedback.mutate({ target, recordId, category })}
          />;
        }) : <EmptyState title="No findings" description="This run has no stored findings to review." />}
      </section>
    </>
  );
}
