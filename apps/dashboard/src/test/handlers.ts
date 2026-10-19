import { http, HttpResponse } from "msw";

export const API = "http://localhost:8000";
export const repository = { id: 12, full_name: "octo/example" };
export const run = { id: 31, repository_id: 12, pull_request_number: 48, head_sha: "a".repeat(40), base_sha: "b".repeat(40), status: "completed", publication_status: "published", created_at: "2026-10-19T10:00:00Z", findings_count: 1, false_count: 1, unverifiable_count: 0 };
export const detail = { ...run, findings: [{ id: 92, claim_id: "claim-92", claim_type: "function_signature", subject: "create_user", claim_text: "The create_user function accepts name and email.", document_path: "docs/api.md", document_line: 14, verdict: "false", explanation: "The function accepts one parameter.", verification_method: "deterministic", deterministic: true, ignored: false, ignore_reason: null, evidence: [{ path: "src/api.py", line: 4, snippet: "def create_user(name):" }], suggestions: [{ id: 7, publication_status: "published", document_path: "docs/api.md", document_line: 14, head_sha: "a".repeat(40), published_at: "2026-10-19T10:01:00Z" }] }] };
export const handlers = [
  http.get(`${API}/auth/session`, () => HttpResponse.json({ github_login: "octocat", github_user_id: 123, expires_at: "2026-10-20T00:00:00Z" })),
  http.get(`${API}/repositories`, () => HttpResponse.json([repository])),
  http.get(`${API}/repositories/:repository_id/runs`, () => HttpResponse.json([run])),
  http.get(`${API}/runs/:run_id`, () => HttpResponse.json(detail)),
  http.get(`${API}/repositories/:repository_id/feedback/metrics`, () => HttpResponse.json({ accepted_fixes: 1, rejected_fixes: 1, false_positives: 0, numerator: 1, denominator: 2, acceptance_rate: 0.5 })),
  http.post(`${API}/suggestions/:suggestion_id/feedback`, async ({ request }) => {
    const body = await request.json() as { category: string };
    return HttpResponse.json({ id: 4, finding_id: 92, run_id: 31, repository_id: 12, category: body.category, reason: null, actor_github_user_id: 123, created_at: "2026-10-19T10:05:00Z" });
  }),
  http.post(`${API}/findings/:finding_id/feedback`, async ({ request }) => {
    const body = await request.json() as { category: string };
    return HttpResponse.json({ id: 5, finding_id: 92, run_id: 31, repository_id: 12, category: body.category, reason: null, actor_github_user_id: 123, created_at: "2026-10-19T10:05:00Z" });
  }),
  http.post(`${API}/auth/logout`, () => new HttpResponse(null, { status: 204 })),
];
