"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "../..");
const FIXTURE = path.join(ROOT, "scripts", "serve_dashboard_fixture.py");
const PYTHON = process.env.PYTHON || "python";
const runningFixtures = new Set();

function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    let forceTimer;
    const terminateTimer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      forceTimer = setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      }, 2_000);
    }, 2_000);
    child.once("exit", () => {
      clearTimeout(terminateTimer);
      clearTimeout(forceTimer);
      resolve();
    });
    child.kill("SIGINT");
  });
}

async function startFixture(options = {}) {
  const args = [FIXTURE, "--port", "0"];
  for (const [name, value] of Object.entries(options)) {
    const flag = `--${name.replaceAll("_", "-")}`;
    if (value === true) args.push(flag);
    else if (value !== false && value !== null && value !== undefined) {
      args.push(flag, String(value));
    }
  }
  const child = spawn(PYTHON, args, {
    cwd: ROOT,
    env: { ...process.env, PYTHONPATH: path.join(ROOT, "src") },
    stdio: ["pipe", "pipe", "pipe"],
  });
  runningFixtures.add(child);
  let stderr = "";
  let stdout = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr += chunk; });

  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error(`dashboard fixture did not start: ${stderr}`));
    }, 8_000);
    child.once("error", (error) => {
      clearTimeout(timeout);
      runningFixtures.delete(child);
      reject(new Error(`could not start dashboard fixture with ${PYTHON}: ${error.message}`));
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      const match = stdout.match(/mxtop dashboard fixture: (http:\/\/\S+)/);
      if (!match) return;
      clearTimeout(timeout);
      resolve(match[1]);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      reject(new Error(
        `dashboard fixture exited before startup (${code ?? signal}): ${stderr}`,
      ));
    });
  });
  return { child, url };
}

function publishStep(fixture, step) {
  fixture.child.stdin.write(`${step}\n`);
}

async function openProcess(page, fixture, theme = "dark") {
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("mxtop-theme", selectedTheme);
  }, theme);
  await page.goto(`${fixture.url}/#/process/atlas-01/0/423901`);
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-detail")).toBeVisible();
}

async function expectNoDocumentOverflow(page) {
  await expect.poll(() => page.evaluate(() => {
    return document.documentElement.scrollWidth - document.documentElement.clientWidth;
  })).toBeLessThanOrEqual(0);
}

function sampleCount(page) {
  return page.locator(".process-metrics-grid")
    .locator("xpath=ancestor::section[1]")
    .locator(".section-count");
}

test.afterEach(async () => {
  const fixtures = [...runningFixtures];
  runningFixtures.clear();
  await Promise.all(fixtures.map(stopProcess));
});

test("opens a live process from the process table", async ({ page }) => {
  const fixture = await startFixture({ step: 5 });
  await page.goto(`${fixture.url}/#/processes`);
  await expect(page.getByRole("heading", { name: "Processes" })).toBeVisible();

  await page.getByRole("link", {
    name: "Open process 423901 on atlas-01 GPU 0",
  }).click();

  await expect(page).toHaveURL(/#\/process\/atlas-01\/0\/423901$/);
  await expect(page.locator(".process-state")).toHaveText("Live");
  await expect(page.locator(".process-metric")).toHaveCount(4);
  await expect(page.locator(".process-metric figcaption").first()).toContainText("now");
  await expect(page.locator(".process-metric figcaption").first()).toContainText("peak");
  await expect(page.locator(".process-command")).toContainText("configs/llama3-70b.yaml");
  await expectNoDocumentOverflow(page);
});

test("preserves control focus through repeated SSE renders", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  const copy = page.getByRole("button", { name: "Copy full process command" });
  await copy.focus();
  const initialSamples = Number((await sampleCount(page).textContent()).match(/\d+/)[0]);

  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText(`${initialSamples + 1} samples`);
  await expect(copy).toBeFocused();
  publishStep(fixture, 2);
  await expect.poll(async () => {
    const text = await sampleCount(page).textContent();
    return Number(text.match(/\d+/)[0]);
  }).toBe(initialSamples + 2);
  await expect(copy).toBeFocused();
});

test("distinguishes node outage, process exit, and PID reuse", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 5,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  await expect(page.locator(".process-state")).toHaveText("Live");
  await expect(page.locator(".process-generation")).toContainText("Generation 1");
  const retainedSamples = await sampleCount(page).textContent();
  const retainedCommand = await page.locator(".process-command").textContent();

  publishStep(fixture, 6);
  await expect(page.locator(".process-state")).toHaveText("Node down");
  await expect(page.locator(".process-state-band")).toContainText("Node unreachable");
  await expect(page.locator(".process-generation")).toContainText("Generation 1");
  await expect(sampleCount(page)).toHaveText(retainedSamples);
  await expect(page.locator(".process-command")).toHaveText(retainedCommand);

  publishStep(fixture, 7);
  await expect(page.locator(".process-state")).toHaveText("Ended");
  await expect(page.locator(".process-state-band")).toContainText("no longer reported");
  await expect(page.locator(".process-generation")).toContainText("Generation 1");
  await expect(sampleCount(page)).toHaveText(retainedSamples);
  await expect(page.locator(".process-command")).toHaveText(retainedCommand);

  publishStep(fixture, 8);
  await expect(page.locator(".process-state")).toHaveText("Live");
  await expect(page.locator(".process-generation")).toContainText("History restarted");
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(page.locator("#app-status")).toContainText("new generation");
});

test("restarts history when a PID is reused between polls", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 5,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  await expect(page.locator(".process-command")).toContainText("train.py");

  publishStep(fixture, 8);

  await expect(page.locator(".process-generation")).toContainText("History restarted");
  await expect(page.locator(".process-generation")).toContainText("Command changed");
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(sampleCount(page)).toHaveText("1 sample");
});

test.describe("responsive process record", () => {
  test.use({ viewport: { width: 320, height: 568 } });

  test("stays within the minimum supported viewport", async ({ page }) => {
    const fixture = await startFixture({
      start_step: 5,
      control_stdin: true,
    });
    await openProcess(page, fixture, "light");
    publishStep(fixture, 6);
    await expect(page.locator(".process-state")).toHaveText("Node down");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect.poll(() => page.locator(".process-metrics-grid").evaluate((grid) => {
      return getComputedStyle(grid).gridTemplateColumns.split(" ").length;
    })).toBe(1);
    await expect.poll(() => page.locator(".process-summary").evaluate((summary) => {
      return getComputedStyle(summary).gridTemplateColumns.split(" ").length;
    })).toBe(2);
    await expectNoDocumentOverflow(page);
  });
});
