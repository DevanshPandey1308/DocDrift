import type { FeedbackCategory, Finding } from "../api/client";
import { Button, FeedbackMessage, StatusBadge } from "./ui";

export function FindingCard({
  finding,
  pending,
  feedbackError,
  feedbackSuccess,
  onFeedback,
}: {
  finding: Finding;
  pending: boolean;
  feedbackError?: string;
  feedbackSuccess?: string;
  onFeedback: (target: "suggestions" | "findings", id: number, category: FeedbackCategory) => void;
}) {
  return (
    <article className={`finding-panel${finding.ignored ? " finding-muted" : ""}`} id={`finding-${finding.id}`}>
      <header className="finding-heading">
        <div className="finding-tags">
          <StatusBadge value={finding.ignored ? "ignored" : finding.verdict} />
          <span className="claim-type">{finding.claim_type.replaceAll("_", " ")}</span>
        </div>
        <a className="file-reference" href={`#finding-${finding.id}`}>
          <span>{finding.document_path}</span><span>:{finding.document_line}</span>
        </a>
      </header>
      <div className="finding-content">
        <section aria-label="Documentation claim">
          <p className="section-label">Documentation claim</p>
          <blockquote>{finding.claim_text}</blockquote>
          <p className="finding-explanation">{finding.explanation}</p>
          {finding.ignored && finding.ignore_reason && <p className="ignore-reason">Ignored: {finding.ignore_reason}</p>}
        </section>

        {finding.evidence.length > 0 ? (
          <section className="evidence-panel" aria-labelledby={`evidence-title-${finding.id}`}>
            <div className="evidence-heading">
              <div><p className="section-label">Source evidence</p><h3 id={`evidence-title-${finding.id}`}>Code references</h3></div>
              <span>{finding.evidence.length} {finding.evidence.length === 1 ? "location" : "locations"}</span>
            </div>
            <ul className="evidence-list">
              {finding.evidence.map((item, index) => (
                <li className="evidence-item" key={`${item.path}:${item.line}:${index}`}>
                  <span className="file-reference"><span>{item.path}</span><span>:{item.line}</span></span>
                  <pre><code>{item.snippet}</code></pre>
                </li>
              ))}
            </ul>
          </section>
        ) : <p className="no-evidence">No source evidence was stored for this finding.</p>}

        {finding.suggestions.filter((suggestion) => suggestion.publication_status === "published").map((suggestion) => (
          <section className="suggestion-panel" key={suggestion.id} aria-label="Published verified suggestion">
            <div className="suggestion-copy">
              <div className="suggestion-title"><StatusBadge value="published" /><strong>Verified suggestion published</strong></div>
              <p><span>{suggestion.document_path}</span>:{suggestion.document_line} · head {suggestion.head_sha.slice(0, 8)}</p>
              <small>The API stores the suggestion’s publication identity, not its proposed diff.</small>
            </div>
            <div className="feedback-actions" aria-label="Suggestion feedback">
              <Button variant="primary" disabled={pending} onClick={() => onFeedback("suggestions", suggestion.id, "fix_accepted")}>Accept</Button>
              <Button disabled={pending} onClick={() => onFeedback("suggestions", suggestion.id, "fix_rejected")}>Reject</Button>
            </div>
            <FeedbackMessage success={feedbackSuccess} error={feedbackError} />
          </section>
        ))}

        {!finding.ignored && (
          <div className="finding-actions">
            <Button variant="quiet" className="false-positive-action" disabled={pending} onClick={() => onFeedback("findings", finding.id, "false_positive")}>
              Report false positive
            </Button>
            {finding.suggestions.every((suggestion) => suggestion.publication_status !== "published") &&
              <FeedbackMessage success={feedbackSuccess} error={feedbackError} />}
          </div>
        )}
      </div>
    </article>
  );
}
