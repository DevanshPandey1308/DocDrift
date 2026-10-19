import createClient from "openapi-fetch";
import { z } from "zod";
import type { paths } from "./schema";

const baseUrl = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const client = createClient<paths>({ baseUrl, credentials: "include" });

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

const SessionSchema = z.object({ github_login: z.string(), github_user_id: z.number(), expires_at: z.string() });
const RepositorySchema = z.object({ id: z.number(), full_name: z.string() });
const RunSchema = z.object({
  id: z.number(), repository_id: z.number(), pull_request_number: z.number(), head_sha: z.string(),
  base_sha: z.string().nullable(), status: z.string(), publication_status: z.string(),
  created_at: z.string(), findings_count: z.number(), false_count: z.number(), unverifiable_count: z.number(),
});
const RunDetailSchema = RunSchema.extend({
  findings: z.array(z.object({
    id: z.number(), claim_id: z.string(), claim_type: z.string(), subject: z.string(), claim_text: z.string(),
    document_path: z.string(), document_line: z.number(), verdict: z.string(), explanation: z.string(),
    verification_method: z.string(), deterministic: z.boolean(), ignored: z.boolean(), ignore_reason: z.string().nullable(),
    evidence: z.array(z.object({ path: z.string(), line: z.number(), snippet: z.string() })),
    suggestions: z.array(z.object({ id: z.number(), publication_status: z.string(), document_path: z.string(),
      document_line: z.number(), head_sha: z.string(), published_at: z.string().nullable() })),
  })),
});
const MetricsSchema = z.object({ accepted_fixes: z.number(), rejected_fixes: z.number(), false_positives: z.number(),
  numerator: z.number(), denominator: z.number(), acceptance_rate: z.number().nullable() });
const FeedbackSchema = z.object({ id: z.number(), finding_id: z.number(), run_id: z.number(), repository_id: z.number(),
  category: z.string(), reason: z.string().nullable(), actor_github_user_id: z.number().nullable(), created_at: z.string() });

async function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): Promise<T> {
  if (!result.response.ok) {
    const payload = typeof result.error === "object" && result.error !== null
      ? result.error as { detail?: unknown; error?: { message?: unknown } }
      : {};
    const message = typeof payload.detail === "string" ? payload.detail
      : typeof payload.error?.message === "string" ? payload.error.message
      : `Request failed (${result.response.status}).`;
    throw new ApiError(result.response.status, message);
  }
  return result.data as T;
}

export async function getSession() {
  return SessionSchema.parse(await unwrap(await client.GET("/auth/session")));
}
export async function getRepositories() {
  return z.array(RepositorySchema).parse(await unwrap(await client.GET("/repositories")));
}
export async function getRuns(repositoryId: number) {
  return z.array(RunSchema).parse(await unwrap(await client.GET("/repositories/{repository_id}/runs", {
    params: { path: { repository_id: repositoryId }, query: { limit: 50, offset: 0 } },
  })));
}
export async function getRun(runId: number) {
  return RunDetailSchema.parse(await unwrap(await client.GET("/runs/{run_id}", { params: { path: { run_id: runId } })));
}
export async function getMetrics(repositoryId: number) {
  return MetricsSchema.parse(await unwrap(await client.GET("/repositories/{repository_id}/feedback/metrics", {
    params: { path: { repository_id: repositoryId } },
  })));
}
export type FeedbackCategory = "fix_accepted" | "fix_rejected" | "false_positive";
export async function submitFeedback(target: "suggestions" | "findings", id: number, category: FeedbackCategory, idempotencyKey = crypto.randomUUID()) {
  const csrf = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith("docdrift_csrf="))?.split("=").slice(1).join("=");
  if (!csrf) throw new ApiError(403, "Your session is missing its CSRF token. Reload and try again.");
  const body = { category, idempotency_key: idempotencyKey };
  const result = target === "suggestions"
    ? await client.POST("/suggestions/{suggestion_id}/feedback", { params: { path: { suggestion_id: id } }, headers: { "X-CSRF-Token": decodeURIComponent(csrf) }, body })
    : await client.POST("/findings/{finding_id}/feedback", { params: { path: { finding_id: id } }, headers: { "X-CSRF-Token": decodeURIComponent(csrf) }, body });
  return FeedbackSchema.parse(await unwrap(result));
}
export async function logout() {
  const csrf = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith("docdrift_csrf="))?.split("=").slice(1).join("=");
  if (!csrf) throw new ApiError(403, "Your session is missing its CSRF token.");
  await unwrap(await client.POST("/auth/logout", { headers: { "X-CSRF-Token": decodeURIComponent(csrf) } }));
}
export function signIn() { window.location.assign(`${baseUrl}/auth/github/login`); }
