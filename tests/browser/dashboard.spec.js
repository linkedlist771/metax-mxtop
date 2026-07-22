"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "../..");
const FIXTURE = path.join(ROOT, "scripts", "serve_dashboard_fixture.py");
const PYTHON = process.env.PYTHON || "python";
const FIXED_TIMESTAMP = 1_768_653_296;
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

function pauseButton(page) {
  return page.getByRole("button", {
    name: "Pause dashboard updates",
    exact: true,
  });
}

function processMetricValue(page, label) {
  return page.locator(".process-metric")
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator(".trend-value");
}

async function expectBuffered(page, count) {
  await expect(page.locator("#pause-count")).toBeVisible();
  await expect(page.locator("#pause-count")).toHaveText(String(count));
  await expect(page.locator("#refresh-state")).toContainText(`${count} buffered`);
}

async function waitForFixtureStep(request, fixture, step) {
  let snapshot = null;
  await expect.poll(async () => {
    const response = await request.get(`${fixture.url}/api/snapshot`);
    snapshot = await response.json();
    return snapshot.timestamp;
  }).toBe(FIXED_TIMESTAMP + step * 2);
  return snapshot;
}

async function readDownloadedJson(page) {
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download JSON" }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  if (!stream) throw new Error("dashboard snapshot download did not provide a stream");
  const chunks = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function snapshotProcess(snapshot, hostname, pid) {
  const node = snapshot.nodes.find((candidate) => candidate.hostname === hostname);
  return node?.frame?.processes.find((process) => process.pid === pid);
}

function sortButton(table, label) {
  return table.locator("th")
    .filter({ hasText: new RegExp(`^${label}$`) })
    .getByRole("button");
}

async function tableColumn(table, index) {
  return table.locator("tbody tr").evaluateAll((rows, columnIndex) => {
    return rows.map((row) => row.cells[columnIndex].textContent.trim());
  }, index);
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

test("buffers frozen process history and resumes at the latest sample", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  const pause = pauseButton(page);
  const gpuUtil = processMetricValue(page, "GPU util");

  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(gpuUtil).toHaveText("58% now | 58% peak");
  await pause.click();
  await expect(pause).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#sample-time")).toContainText("Paused");
  const pausedTime = await page.locator("#sample-time").textContent();

  for (const step of [1, 2, 3]) {
    publishStep(fixture, step);
    await expectBuffered(page, step);
  }

  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator("#sample-time")).toHaveText(pausedTime);
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(gpuUtil).toHaveText("58% now | 58% peak");

  await pause.click();
  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#refresh-state")).not.toContainText("buffered");
  await expect(page.locator("#sample-time")).not.toContainText("Paused");
  await expect(sampleCount(page)).toHaveText("4 samples");
  await expect(gpuUtil).toHaveText("91% now | 91% peak");

  publishStep(fixture, 4);
  await expect(sampleCount(page)).toHaveText("5 samples");
  await expect(gpuUtil).toHaveText("84% now | 91% peak");
});

test("keeps routes, search, and downloads frozen before PID-reuse resume", async ({ page, request }) => {
  const fixture = await startFixture({
    start_step: 5,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  const pause = pauseButton(page);
  await pause.click();

  for (const step of [6, 7, 8]) {
    publishStep(fixture, step);
    await expectBuffered(page, step - 5);
  }
  const latestSnapshot = await waitForFixtureStep(request, fixture, 8);
  const latestProcess = snapshotProcess(latestSnapshot, "atlas-01", 423901);
  expect(latestProcess.command).toContain("inference.server");
  expect(latestProcess.user).toBe("service");

  await expect(page.locator(".process-state")).toHaveText("Live");
  await expect(page.locator(".process-generation")).toContainText("Generation 1");
  await expect(page.locator(".process-command")).toContainText("train.py");
  await expect(sampleCount(page)).toHaveText("1 sample");

  await page.getByRole("button", { name: "Processes", exact: true }).click();
  const search = page.getByRole("searchbox", {
    name: "Search node, GPU, PID, user, or command",
  });
  await search.fill("service");
  await expect(page.locator(".result-count")).toHaveText("0 rows");
  await expect(page.getByText("inference.server")).toHaveCount(0);
  await expect(pause).toHaveAttribute("aria-pressed", "true");
  await expectBuffered(page, 3);

  const frozenSnapshot = await readDownloadedJson(page);
  const frozenProcess = snapshotProcess(frozenSnapshot, "atlas-01", 423901);
  expect(frozenSnapshot.timestamp).toBe(FIXED_TIMESTAMP + 5 * 2);
  expect(frozenProcess.command).toContain("train.py");
  expect(frozenProcess.user).toBe("alice");

  await page.goBack();
  await expect(page.locator(".process-command")).toContainText("train.py");
  await pause.click();
  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(page.locator(".process-generation")).toContainText("History restarted");
  await expect(page.locator(".process-generation")).toContainText("PID returned after ending");
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(page.locator("#app-status")).toContainText(
    "Dashboard updates resumed",
  );
  await expect(page.locator("#app-status")).toContainText(
    "Process 423901 started a new generation",
  );
});

test("supports p and uppercase Z without stealing focus or capturing typed text", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await page.goto(`${fixture.url}/#/processes`);
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  const pause = pauseButton(page);
  const table = page.getByRole("table", { name: "Processes" });
  const runtime = sortButton(table, "Runtime");
  await runtime.click();
  await expect(runtime).toBeFocused();

  await page.keyboard.press("p");
  await expect(pause).toHaveAttribute("aria-pressed", "true");
  await expect(runtime).toBeFocused();
  publishStep(fixture, 1);
  await expectBuffered(page, 1);
  await expect(runtime).toBeFocused();

  await page.keyboard.press("Shift+Z");
  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await expect(runtime).toBeFocused();
  await expect(runtime.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "descending",
  );

  const search = page.getByRole("searchbox", {
    name: "Search node, GPU, PID, user, or command",
  });
  await search.focus();
  await page.keyboard.type("pZ");
  await expect(search).toHaveValue("pZ");
  await expect(pause).toHaveAttribute("aria-pressed", "false");

  await page.keyboard.press("Escape");
  await expect(search).not.toBeFocused();
  await page.keyboard.press("z");
  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await page.keyboard.press("Shift+Z");
  await expect(pause).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("p");
  await expect(pause).toHaveAttribute("aria-pressed", "false");
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

test("sorts and filters processes with accessible column state", async ({ page }) => {
  const fixture = await startFixture({ step: 5 });
  await page.goto(`${fixture.url}/#/processes`);
  const table = page.getByRole("table", { name: "Processes" });
  await expect(table).toBeVisible();
  await expect(page.getByRole("region", { name: /Processes table/ })).toHaveCount(0);

  const gpuMemory = sortButton(table, "GPU memory");
  await expect(table.locator("th[aria-sort]")).toHaveCount(1);
  await expect(gpuMemory.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  expect(await tableColumn(table, 2)).toEqual(["423901", "781044", "424250"]);

  const runtime = sortButton(table, "Runtime");
  await expect(runtime).toHaveAccessibleName("Runtime, sort descending");
  await expect(runtime.locator("xpath=ancestor::th")).not.toHaveAttribute("aria-sort");
  await runtime.click();
  await expect(runtime).toBeFocused();
  await expect(runtime).toHaveAccessibleName(
    "Runtime, sorted descending; sort ascending",
  );
  await expect(runtime.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  await expect.poll(() => runtime.evaluate((button) => {
    return getComputedStyle(button, "::after").content;
  })).toContain("\u2193");
  expect(await tableColumn(table, 2)).toEqual(["423901", "424250", "781044"]);

  await runtime.press(" ");
  await expect(runtime).toBeFocused();
  await expect(runtime.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "ascending",
  );
  await expect(page.locator("#app-status")).toContainText(
    "Processes sorted by Runtime, ascending",
  );
  expect(await tableColumn(table, 2)).toEqual(["781044", "424250", "423901"]);

  await page.getByRole("searchbox", {
    name: "Search node, GPU, PID, user, or command",
  }).fill("atlas-01");
  expect(await tableColumn(table, 2)).toEqual(["424250", "423901"]);
  await expect(runtime.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "ascending",
  );
});

test("keeps node sort focus and missing values stable across SSE", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 5,
    control_stdin: true,
  });
  await page.goto(`${fixture.url}/#/nodes`);
  const table = page.getByRole("table", { name: "Nodes" });
  await expect(table).toBeVisible();

  const stateHeader = sortButton(table, "State");
  await expect(table.locator("th[aria-sort]")).toHaveCount(1);
  await expect(stateHeader).toHaveAccessibleName(
    "State, sorted ascending, Down first; sort descending, Online first",
  );
  await expect(stateHeader.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "ascending",
  );
  const cpu = sortButton(table, "CPU");
  await cpu.click();
  await expect(stateHeader).toHaveAccessibleName(
    "State, sort ascending, Down first",
  );
  await expect(cpu).toBeFocused();
  expect(await tableColumn(table, 0)).toEqual(["atlas-01", "borealis-02"]);

  await cpu.press("Enter");
  await expect(cpu.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "ascending",
  );
  expect(await tableColumn(table, 0)).toEqual(["borealis-02", "atlas-01"]);

  publishStep(fixture, 6);
  await expect(table.locator(".node-state.offline")).toHaveText("Down");
  expect(await tableColumn(table, 0)).toEqual(["borealis-02", "atlas-01"]);
  await expect(cpu).toBeFocused();
  await expect(cpu.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "ascending",
  );

  await cpu.press("Enter");
  await expect(cpu.locator("xpath=ancestor::th")).toHaveAttribute(
    "aria-sort",
    "descending",
  );
  expect(await tableColumn(table, 0)).toEqual(["borealis-02", "atlas-01"]);

  await stateHeader.click();
  await expect(page.locator("#app-status")).toContainText(
    "Nodes sorted by State, ascending, Down first",
  );
  await stateHeader.click();
  await expect(page.locator("#app-status")).toContainText(
    "Nodes sorted by State, descending, Online first",
  );
});

test.describe("responsive process record", () => {
  test.use({ viewport: { width: 320, height: 568 }, hasTouch: true });

  test("stays within the minimum supported viewport", async ({ page }) => {
    const fixture = await startFixture({
      start_step: 5,
      control_stdin: true,
    });
    await openProcess(page, fixture, "light");
    const pause = pauseButton(page);
    await expect(pause).toHaveAccessibleName("Pause dashboard updates");
    await pause.tap();
    publishStep(fixture, 6);
    await expectBuffered(page, 1);
    await expect(page.locator(".process-state")).toHaveText("Live");
    await expect(page.locator("#sample-time")).toContainText("Paused");
    const pauseBox = await pause.boundingBox();
    expect(pauseBox.width).toBeGreaterThanOrEqual(44);
    expect(pauseBox.height).toBeGreaterThanOrEqual(44);
    await expect.poll(() => page.locator(".app-footer").evaluate((footer) => {
      return footer.scrollWidth <= footer.clientWidth;
    })).toBe(true);
    await expectNoDocumentOverflow(page);

    await pause.tap();
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

  test("contains sortable process headers inside the table scroller", async ({ page }) => {
    const fixture = await startFixture({ step: 5 });
    await page.goto(`${fixture.url}/#/processes`);
    const table = page.getByRole("table", { name: "Processes" });
    const wrap = page.getByRole("region", {
      name: "Processes table, horizontally scrollable",
    });
    const runtime = sortButton(table, "Runtime");

    await expect(wrap).toHaveAttribute("tabindex", "0");
    await runtime.scrollIntoViewIfNeeded();
    const scrollBeforeSort = await wrap.evaluate((node) => node.scrollLeft);
    await runtime.click();

    await expect(runtime.locator("xpath=ancestor::th")).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    await expect.poll(() => wrap.evaluate((node) => node.scrollWidth > node.clientWidth))
      .toBe(true);
    await expect.poll(() => wrap.evaluate((node) => node.scrollLeft))
      .toBe(scrollBeforeSort);
    await wrap.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
    await expect.poll(() => table.locator("th").last().evaluate((header) => {
      const headerRect = header.getBoundingClientRect();
      const wrapRect = header.closest(".table-wrap").getBoundingClientRect();
      const visibleWidth = Math.min(headerRect.right, wrapRect.right)
        - Math.max(headerRect.left, wrapRect.left);
      return visibleWidth >= 40 && headerRect.right <= wrapRect.right + 1;
    })).toBe(true);
    await expect.poll(() => table.locator("tbody td").first().evaluate((cell) => {
      return getComputedStyle(cell).position;
    })).toBe("sticky");
    await expectNoDocumentOverflow(page);

    const rawWrap = page.locator('[data-scroll-key="process-table"]');
    await rawWrap.focus();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(rawWrap).toBeFocused();
    await expect(rawWrap).toHaveAttribute("role", "region");
    await expect(rawWrap).toHaveAttribute("aria-label", "Processes table");
    await page.getByRole("button", { name: "Overview" }).focus();
    await expect(rawWrap).not.toHaveAttribute("role");
    await expect(rawWrap).toHaveAttribute("tabindex", "-1");
  });
});
