import { expect, test } from "@playwright/test";

test("dashboard shell loads", async ({ page }) => {
  await page.route("**/auth/session", async (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Authentication required." }) }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /sign in with github/i })).toBeVisible();
});
