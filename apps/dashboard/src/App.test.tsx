import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import App from "./App";
import { API } from "./test/handlers";
import { server } from "./test/server";

function mount(path = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

describe("dashboard", () => {
  it("shows a real unauthenticated state for a 401 session", async () => {
    server.use(http.get(`${API}/auth/session`, () => HttpResponse.json({ detail: "Authentication required." }, { status: 401 })));
    mount();
    expect(await screen.findByRole("button", { name: /sign in with github/i })).toBeInTheDocument();
    expect(screen.queryByText("octo/example")).not.toBeInTheDocument();
  });

  it("shows progress while the session check is pending", async () => {
    server.use(http.get(`${API}/auth/session`, async () => {
      await delay(120);
      return HttpResponse.json({ github_login: "octocat", github_user_id: 123, expires_at: "2026-10-20T00:00:00Z" });
    }));
    mount();
    expect(screen.getByRole("status")).toHaveTextContent(/checking github session/i);
    expect(await screen.findByRole("heading", { name: "Overview" })).toBeInTheDocument();
  });

  it("renders authenticated overview and navigates to repository runs", async () => {
    const user = userEvent.setup(); mount();
    expect(await screen.findByText("octo/example")).toBeInTheDocument();
    await user.click(screen.getAllByText("octo/example")[0]);
    expect(await screen.findByRole("heading", { name: "octo/example" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /pull request 48/i })).toBeInTheDocument();
  });

  it("shows finding claim, source evidence, and records suggestion feedback", async () => {
    document.cookie = "docdrift_csrf=test-token; path=/";
    const user = userEvent.setup(); mount("/runs/31");
    expect(await screen.findByText("The create_user function accepts name and email.")).toBeInTheDocument();
    expect(screen.getByText("src/api.py:4")).toBeInTheDocument();
    expect(screen.getByText("def create_user(name):")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("status", { name: /feedback recorded/i })).toBeInTheDocument();
  });

  it("reuses the same idempotency key when the maintainer retries a failed feedback request", async () => {
    document.cookie = "docdrift_csrf=test-token; path=/";
    const keys: string[] = [];
    let attempts = 0;
    server.use(http.post(`${API}/suggestions/:suggestion_id/feedback`, async ({ request }) => {
      const body = await request.json() as { idempotency_key: string };
      keys.push(body.idempotency_key);
      attempts += 1;
      if (attempts === 1) return HttpResponse.json({ detail: "Temporary failure" }, { status: 503 });
      return HttpResponse.json({ id: 4, finding_id: 92, run_id: 31, repository_id: 12, category: "fix_accepted", reason: null, actor_github_user_id: 123, created_at: "2026-10-19T10:05:00Z" });
    }));
    const user = userEvent.setup(); mount("/runs/31");
    await user.click(await screen.findByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/retry the same action/i);
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByRole("status", { name: /feedback recorded/i })).toBeInTheDocument();
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it("submits rejection and false-positive feedback through their eligible controls", async () => {
    document.cookie = "docdrift_csrf=test-token; path=/";
    const user = userEvent.setup(); mount("/runs/31");
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Feedback recorded: fix rejected.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /report false positive/i }));
    expect(await screen.findByText("Feedback recorded: false positive.")).toBeInTheDocument();
  });

  it("renders an empty repository state", async () => {
    server.use(http.get(`${API}/repositories`, () => HttpResponse.json([])));
    mount("/repositories");
    expect(await screen.findByText("No repositories available")).toBeInTheDocument();
  });

  it("reports forbidden API responses without showing private data", async () => {
    server.use(http.get(`${API}/repositories`, () => HttpResponse.json({ detail: "Forbidden" }, { status: 403 })));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent(/access denied/i);
  });

  it("handles null acceptance rate as no outcomes", async () => {
    server.use(http.get(`${API}/repositories/:repository_id/feedback/metrics`, () => HttpResponse.json({ accepted_fixes: 0, rejected_fixes: 0, false_positives: 1, numerator: 0, denominator: 0, acceptance_rate: null })));
    mount("/runs/31");
    expect(await screen.findByText("Acceptance rate")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("—").length).toBeGreaterThan(0));
  });

  it("offers keyboard-operable repository navigation", async () => {
    mount();
    const link = await screen.findByRole("link", { name: /octo\/example/i });
    link.focus();
    expect(link).toHaveFocus();
    fireEvent.keyDown(link, { key: "Enter" });
  });
});
