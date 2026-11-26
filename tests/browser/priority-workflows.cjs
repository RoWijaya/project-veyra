const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium, firefox, webkit } = require(process.env.VEYRA_PLAYWRIGHT_MODULE || "playwright-core");

const [engine, fixturesPath] = process.argv.slice(2);
const fixture = JSON.parse(fs.readFileSync(fixturesPath, "utf8"))[engine];
const origin = process.env.VEYRA_BUILDER_TEST_ORIGIN || "http://127.0.0.1:8112";
const browserType = { chrome: chromium, firefox, webkit }[engine];
const launch = engine === "chrome" && process.env.VEYRA_CHROME_EXECUTABLE
  ? { executablePath: process.env.VEYRA_CHROME_EXECUTABLE } : {};

async function checkWidths(page) {
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Overflow at " + width);
  }
}

(async () => {
  const browser = await browserType.launch(launch);
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 900 }, reducedMotion: "reduce" });
    await page.goto(origin + "/register/");
    await page.locator("input[name=first_name]").fill("Browser verification");
    await page.locator("input[name=email]").fill("verification-" + engine + "@example.invalid");
    await page.locator("input[name=password1]").fill("Passphrase-For-Synthetic-Signup-2026!");
    await page.locator("input[name=password2]").fill("Passphrase-For-Synthetic-Signup-2026!");
    await page.locator("form button[type=submit]").first().click();
    await page.getByRole("heading", { name: "Check your email" }).waitFor();
    await page.getByText(/retry temporary delivery failures/).waitFor();
    await checkWidths(page);
    await page.getByRole("link", { name: "Request another email" }).click();
    await page.locator("input[name=email]").fill("verification-" + engine + "@example.invalid");
    await page.locator("form button[type=submit]").first().click();
    await page.getByRole("heading", { name: "Check your email" }).waitFor();

    await page.getByRole("link", { name: "Back to sign in" }).click();
    await page.locator("input[name=email]").fill(fixture.email);
    await page.locator("input[name=password]").fill(fixture.password);
    await page.locator("form button[type=submit]").first().click();
    await page.goto(origin + fixture.root + "payments/");
    const form = page.locator("form[data-provider-form]");
    await form.locator("[name=provider]").selectOption("bca_qris");
    await form.locator("[name=environment]").selectOption("sandbox");
    await form.locator("[name=merchant_id]").fill("qa-merchant-" + engine);
    await form.locator("[name=client_id]").fill("synthetic-client-id");
    const clientSecret = "Synthetic-Browser-Key-" + engine;
    await form.locator("[name=client_secret]").fill(clientSecret);
    await form.getByRole("button", { name: "Encrypt and save" }).click();
    await page.getByText("Awaiting verification").waitFor();
    assert(!((await page.content()).includes(clientSecret)), "Credential appeared in the rendered page.");
    await page.getByRole("button", { name: "Verify connection" }).click();
    await page.getByText("Provider approval required", { exact: true }).waitFor();
    await checkWidths(page);
    console.log(engine + " passed signup, resend and merchant configuration at four widths.");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
