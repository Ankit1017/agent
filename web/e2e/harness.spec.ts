import { expect, test } from "@playwright/test";

test("renders the local harness shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Local AI Harness")).toBeVisible();
  await expect(page.getByLabel("Prompt")).toBeVisible();
});

test("voice profile editor is scrollable and navigates between speech modes", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto("/speech/agents");
  await expect(
    page.getByRole("heading", { name: "Create profile" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "New profile" })).toBeVisible();
  const editor = page.locator(".agent-profile-editor");
  expect(
    await editor.evaluate(
      (element) => element.scrollHeight > element.clientHeight,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "Speech" }).click();
  await page.getByRole("button", { name: "Direct Text-to-Speech" }).click();
  await expect(
    page.getByRole("button", { name: "Direct Text-to-Speech" }),
  ).toHaveAttribute("aria-current", "page");
  expect(errors).toEqual([]);
});

for (const width of [390, 768, 1024, 1440]) {
  test(`shared workspace does not overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ["/", "/speech", "/speech/agents", "/studio"]) {
      await page.goto(route);
      await expect(page.locator(".app-header")).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);
    }
  });
}

test("voice pages remain usable at a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/speech/agents");
  await expect(
    page.getByRole("button", { name: "Save profile" }),
  ).toBeVisible();
  await expect(page.getByText("Voice Output", { exact: true })).toBeVisible();
  await page.goto("/speech");
  await expect(
    page.getByRole("button", { name: "New conversation" }),
  ).toBeVisible();
  await expect(page.getByLabel("Voice conversation message")).toBeVisible();
});
