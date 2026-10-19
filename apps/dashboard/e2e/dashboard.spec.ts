import { expect, test } from "@playwright/test";

test("dashboard shell loads", async ({ page }) => {
  await page.route("**/auth/session", async (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Authentication required." }) }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /sign in with github/i })).toBeVisible();
});

test("authenticated navigation remains available on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/auth/session", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ github_login: "octocat", github_user_id: 123, expires_at: "2026-10-20T00:00:00Z" }) }));
  await page.route("**/repositories", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
  await page.getByRole("link", { name: "Repositories" }).click();
  await expect(page.getByRole("heading", { name: "Repositories" })).toBeVisible();
  await expect(page.getByText("No repositories available")).toBeVisible();
});
