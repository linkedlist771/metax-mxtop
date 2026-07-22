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

function publishProfileStep(fixture, profile, step) {
  fixture.child.stdin.write(`${JSON.stringify({ profile, step })}\n`);
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

async function waitForFixtureProfile(request, fixture, hostname, step) {
  await expect.poll(async () => {
    const response = await request.get(`${fixture.url}/api/snapshot`);
    const snapshot = await response.json();
    return `${snapshot.nodes[0]?.hostname}:${snapshot.timestamp}`;
  }).toBe(`${hostname}:${FIXED_TIMESTAMP + step * 2}`);
}

async function currentHistoryScope(page) {
  return page.evaluate(async () => {
    const storage = window.mxtopHistoryStorage;
    if (!storage) throw new Error("dashboard history storage is unavailable");
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
    const result = await storage.scopeForCluster(await response.json());
    if (!result.ok || !result.scope) {
      throw new Error(`could not compute history scope: ${result.status}`);
    }
    return result.scope;
  });
}

async function historyDatabaseSnapshot(page) {
  return page.evaluate(async () => {
    const storage = window.mxtopHistoryStorage;
    if (!storage) throw new Error("dashboard history storage is unavailable");
    const { DB_NAME, DB_VERSION, STORE_NAME } = storage.constants;
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("history database open blocked"));
    });
    try {
      const records = await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE_NAME, "readonly");
        const request = transaction.objectStore(STORE_NAME).getAll();
        let values = [];
        request.onsuccess = () => { values = request.result; };
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => resolve(values);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
      const raw = JSON.stringify(records, (_key, value) => {
        if (value instanceof ArrayBuffer) {
          return { bytes: [...new Uint8Array(value)] };
        }
        if (ArrayBuffer.isView(value)) {
          return {
            bytes: [...new Uint8Array(value.buffer, value.byteOffset, value.byteLength)],
          };
        }
        return value;
      });
      const binaryHex = records.flatMap((record) => Object.values(record)
        .map((value) => {
          const bytes = value instanceof ArrayBuffer
            ? new Uint8Array(value)
            : ArrayBuffer.isView(value)
              ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
              : null;
          return bytes
            ? [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")
            : null;
        })
        .filter(Boolean)).join(":");
      return {
        raw,
        binaryHex,
        records: records.map((record) => ({
          id: record.id,
          scope: record.scope,
          sessionId: record.sessionId,
          savedAtMs: record.savedAtMs,
          expiresAtMs: record.expiresAtMs,
          lastTimestamp: record.lastTimestamp,
          ciphertextBytes: record.ciphertext instanceof ArrayBuffer
            ? record.ciphertext.byteLength
            : ArrayBuffer.isView(record.ciphertext) ? record.ciphertext.byteLength : 0,
          keys: Object.keys(record).sort(),
        })),
      };
    } finally {
      database.close();
    }
  });
}

async function waitForStoredHistory(page, scope, step, previousId = null) {
  let record = null;
  const expectedTimestamp = FIXED_TIMESTAMP + step * 2;
  await expect.poll(async () => {
    const database = await historyDatabaseSnapshot(page);
    record = database.records
      .filter((candidate) => candidate.scope === scope)
      .sort((left, right) => right.savedAtMs - left.savedAtMs)[0] || null;
    if (!record || (previousId && record.id === previousId)) return null;
    return record.lastTimestamp;
  }).toBe(expectedTimestamp);
  return record;
}

async function currentHistorySessionId(page) {
  return page.evaluate(() => {
    const key = window.mxtopHistoryStorage?.constants?.SESSION_STORAGE_KEY;
    const material = key ? sessionStorage.getItem(key) : null;
    return material ? JSON.parse(material).sessionId : null;
  });
}

async function waitForStoredSessionHistory(page, sessionId, step) {
  let record = null;
  const expectedTimestamp = FIXED_TIMESTAMP + step * 2;
  await expect.poll(async () => {
    const database = await historyDatabaseSnapshot(page);
    record = database.records.find((candidate) => candidate.sessionId === sessionId) || null;
    return record?.lastTimestamp ?? null;
  }).toBe(expectedTimestamp);
  return record;
}

async function tamperStoredCiphertext(page, scope) {
  await page.evaluate(async (targetScope) => {
    const storage = window.mxtopHistoryStorage;
    if (!storage) throw new Error("dashboard history storage is unavailable");
    const { DB_NAME, DB_VERSION, STORE_NAME } = storage.constants;
    const database = await new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("history database open blocked"));
    });
    try {
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE_NAME, "readwrite");
        const store = transaction.objectStore(STORE_NAME);
        const request = store.getAll();
        let operationError = null;
        request.onsuccess = () => {
          const record = request.result.find((candidate) => candidate.scope === targetScope);
          if (!record) {
            operationError = new Error("history record not found");
            transaction.abort();
            return;
          }
          const ciphertext = new Uint8Array(record.ciphertext.slice(0));
          ciphertext[0] ^= 0xff;
          record.ciphertext = ciphertext.buffer;
          store.put(record);
        };
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(operationError || transaction.error);
      });
    } finally {
      database.close();
    }
  }, scope);
}

async function loadStoredHistory(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
    return window.mxtopHistoryStorage.load(await response.json());
  });
}

async function saveTimestampMismatchedHistory(page) {
  return page.evaluate(async () => {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`snapshot request failed: ${response.status}`);
    const cluster = await response.json();
    const loaded = await window.mxtopHistoryStorage.load(cluster);
    if (!loaded.ok || loaded.status !== "loaded") {
      throw new Error(`could not load history before mismatch test: ${loaded.status}`);
    }
    return window.mxtopHistoryStorage.save(
      cluster,
      loaded.payload,
      cluster.timestamp + 1,
    );
  });
}

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => { errors.push(error.message); });
  return errors;
}

function expectNoStoredPlaintext(database, plaintext) {
  expect(database.raw.toLowerCase()).not.toContain(plaintext.toLowerCase());
  expect(database.binaryHex).not.toContain(Buffer.from(plaintext, "utf8").toString("hex"));
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
  await expect(page.locator("#app-status")).toHaveText(
    "Current cluster loaded. No saved incident history was found.",
  );

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

test("hydrates encrypted process history exactly once after reload", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  const pageErrors = trackPageErrors(page);
  await openProcess(page, fixture);
  const scope = await currentHistoryScope(page);
  await waitForStoredHistory(page, scope, 0);

  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText("2 samples");
  await waitForStoredHistory(page, scope, 1);
  publishStep(fixture, 2);
  await expect(sampleCount(page)).toHaveText("3 samples");
  await expect(processMetricValue(page, "GPU util")).toHaveText("79% now | 79% peak");
  const stored = await waitForStoredHistory(page, scope, 2);
  expect(stored.ciphertextBytes).toBeGreaterThan(16);
  expect(stored.keys).not.toContain("payload");

  const database = await historyDatabaseSnapshot(page);
  expectNoStoredPlaintext(database, "atlas-01");
  expectNoStoredPlaintext(database, "alice");
  expectNoStoredPlaintext(database, "train.py");

  await page.reload();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-detail")).toBeVisible();
  await expect(sampleCount(page)).toHaveText("3 samples");
  await expect(processMetricValue(page, "GPU util")).toHaveText("79% now | 79% peak");
  await expect(page.locator("#app-status")).toContainText(
    "Restored 3 incident history samples",
  );

  publishStep(fixture, 3);
  await expect(sampleCount(page)).toHaveText("4 samples");
  await expect(processMetricValue(page, "GPU util")).toHaveText("91% now | 91% peak");
  await waitForStoredHistory(page, scope, 3);
  expect(pageErrors).toEqual([]);
});

test("does not overwrite valid history when hydration exceeds its deadline", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  const scope = await currentHistoryScope(page);
  await waitForStoredHistory(page, scope, 0);
  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText("2 samples");
  await waitForStoredHistory(page, scope, 1);
  publishStep(fixture, 2);
  await expect(sampleCount(page)).toHaveText("3 samples");
  await waitForStoredHistory(page, scope, 2);

  await page.addInitScript(() => {
    let wrappedStorage = null;
    window.__historyLoadSettled = false;
    window.__historySaveCalls = 0;
    Object.defineProperty(window, "mxtopHistoryStorage", {
      configurable: true,
      get() { return wrappedStorage; },
      set(storage) {
        wrappedStorage = Object.freeze({
          ...storage,
          load: async (...args) => {
            await new Promise((resolve) => { setTimeout(resolve, 1_200); });
            const result = await storage.load(...args);
            window.__historyLoadSettled = true;
            return result;
          },
          save: (...args) => {
            window.__historySaveCalls += 1;
            return storage.save(...args);
          },
        });
      },
    });
  });

  await page.reload();
  await expect(page.locator("#app")).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "Download JSON" })).toBeDisabled();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(page.locator("#app-status")).toContainText("did not load in time");
  await expect(page.locator("#app")).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("button", { name: "Download JSON" })).toBeEnabled();
  await expect.poll(() => page.evaluate(() => window.__historyLoadSettled)).toBe(true);
  await page.waitForTimeout(650);
  expect(await page.evaluate(() => window.__historySaveCalls)).toBe(0);

  publishStep(fixture, 3);
  await expect(sampleCount(page)).toHaveText("2 samples");
  await page.waitForTimeout(650);
  expect(await page.evaluate(() => window.__historySaveCalls)).toBe(0);

  const retainedSamples = await page.evaluate(async () => {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    const result = await window.mxtopHistoryStorage.load(await response.json());
    return result.payload?.timestamps?.length;
  });
  expect(retainedSamples).toBe(3);
});

test("rejects tampered ciphertext and authenticated malformed history", async ({ page }) => {
  const fixture = await startFixture({ step: 0 });
  const pageErrors = trackPageErrors(page);
  await openProcess(page, fixture);
  const scope = await currentHistoryScope(page);
  await waitForStoredHistory(page, scope, 0);

  const invalidSave = await saveTimestampMismatchedHistory(page);
  expect(invalidSave).toMatchObject({ ok: true, status: "saved" });
  await page.reload();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-detail")).toBeVisible();
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(page.locator("#app-status")).toContainText(
    "Saved incident history was invalid and was not restored.",
  );
  await waitForStoredHistory(page, scope, 0);

  await tamperStoredCiphertext(page, scope);
  const tamperedLoad = await loadStoredHistory(page);
  expect(tamperedLoad).toMatchObject({ ok: false, status: "invalid", payload: null });
  expect((await historyDatabaseSnapshot(page)).records).toHaveLength(0);
  expect(pageErrors).toEqual([]);
});

test("restores exited and reused processes and clears persisted history", async ({ page }) => {
  const fixture = await startFixture({
    start_step: 5,
    control_stdin: true,
  });
  const pageErrors = trackPageErrors(page);
  await openProcess(page, fixture);
  const scope = await currentHistoryScope(page);
  await waitForStoredHistory(page, scope, 5);

  publishStep(fixture, 6);
  await expect(page.locator(".process-state")).toHaveText("Node down");
  await waitForStoredHistory(page, scope, 6);
  publishStep(fixture, 7);
  await expect(page.locator(".process-state")).toHaveText("Ended");
  await expect(page.locator(".process-command")).toContainText("train.py");
  await waitForStoredHistory(page, scope, 7);

  await page.reload();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-state")).toHaveText("Ended");
  await expect(page.locator(".process-generation")).toContainText("Generation 1");
  await expect(page.locator(".process-command")).toContainText("train.py");
  await expect(sampleCount(page)).toHaveText("1 sample");

  publishStep(fixture, 8);
  await expect(page.locator(".process-state")).toHaveText("Live");
  await expect(page.locator(".process-generation")).toContainText("History restarted");
  await expect(page.locator(".process-generation")).toContainText("PID returned after ending");
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(sampleCount(page)).toHaveText("1 sample");
  await waitForStoredHistory(page, scope, 8);

  await page.reload();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-generation")).toContainText("History restarted");
  await expect(page.locator(".process-generation")).toContainText("PID returned after ending");
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(sampleCount(page)).toHaveText("1 sample");

  publishStep(fixture, 9);
  await expect(sampleCount(page)).toHaveText("2 samples");
  const beforeClear = await waitForStoredHistory(page, scope, 9);
  await page.getByRole("button", { name: "Clear history", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Clear incident history?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.locator("#app-status")).toContainText(
    "Incident history cleared. The current sample remains.",
  );
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(processMetricValue(page, "GPU util")).toHaveText("41% now | 41% peak");
  const afterClear = await waitForStoredHistory(page, scope, 9, beforeClear.id);
  expect(afterClear.id).not.toBe(beforeClear.id);
  const afterClearDatabase = await historyDatabaseSnapshot(page);
  expect(afterClearDatabase.records.map((record) => record.id)).toEqual([afterClear.id]);
  expect(afterClearDatabase.records.map((record) => record.id)).not.toContain(beforeClear.id);

  const pause = pauseButton(page);
  const dialogElement = page.locator("#clear-history-dialog");
  await page.getByRole("button", { name: "Clear history", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(dialogElement).toHaveJSProperty("returnValue", "");
  await page.keyboard.press("p");
  await expect(pause).toHaveAttribute("aria-pressed", "false");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(dialogElement).toHaveJSProperty("returnValue", "");

  await page.reload();
  await expect(page.locator(".connection-state")).toHaveClass(/live/);
  await expect(page.locator(".process-command")).toContainText("inference.server");
  await expect(sampleCount(page)).toHaveText("1 sample");
  await expect(processMetricValue(page, "GPU util")).toHaveText("41% now | 41% peak");
  expect(pageErrors).toEqual([]);
});

test("clears only the current tab session's encrypted history", async ({ page, context }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await openProcess(page, fixture);
  const firstSession = await currentHistorySessionId(page);
  expect(firstSession).toMatch(/^[0-9a-f]{32}$/);
  await waitForStoredSessionHistory(page, firstSession, 0);

  const otherPage = await context.newPage();
  await openProcess(otherPage, fixture);
  const otherSession = await currentHistorySessionId(otherPage);
  expect(otherSession).toMatch(/^[0-9a-f]{32}$/);
  expect(otherSession).not.toBe(firstSession);
  await waitForStoredSessionHistory(page, otherSession, 0);

  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText("2 samples");
  await expect(sampleCount(otherPage)).toHaveText("2 samples");
  await waitForStoredSessionHistory(page, firstSession, 1);
  await waitForStoredSessionHistory(page, otherSession, 1);

  await page.getByRole("button", { name: "Clear history", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Clear incident history?" });
  await dialog.getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.locator("#app-status")).toContainText(
    "Incident history cleared. The current sample remains.",
  );
  await expect.poll(() => currentHistorySessionId(page)).toMatch(/^[0-9a-f]{32}$/);
  const replacementSession = await currentHistorySessionId(page);
  expect(replacementSession).not.toBe(firstSession);
  await waitForStoredSessionHistory(page, replacementSession, 1);

  const database = await historyDatabaseSnapshot(page);
  expect(new Set(database.records.map((record) => record.sessionId))).toEqual(
    new Set([replacementSession, otherSession]),
  );
  expect(database.records.map((record) => record.sessionId)).not.toContain(firstSession);

  await otherPage.reload();
  await expect(otherPage.locator(".connection-state")).toHaveClass(/live/);
  await expect(sampleCount(otherPage)).toHaveText("2 samples");
  await expect(otherPage.locator("#app-status")).toContainText(
    "Restored 2 incident history samples",
  );
  await otherPage.close();
});

test("partitions encrypted history when fixture profiles switch on one origin", async ({ page, request }) => {
  const fixture = await startFixture({
    start_step: 0,
    control_stdin: true,
  });
  await page.addInitScript(() => {
    const nativeSetTimeout = window.setTimeout.bind(window);
    window.__holdHistoryDebounce = false;
    window.setTimeout = (callback, delay, ...args) => {
      const effectiveDelay = delay === 500 && window.__holdHistoryDebounce
        ? 60_000
        : delay;
      return nativeSetTimeout(callback, effectiveDelay, ...args);
    };
  });
  const pageErrors = trackPageErrors(page);
  await openProcess(page, fixture);
  const alphaScope = await currentHistoryScope(page);
  await waitForStoredHistory(page, alphaScope, 0);

  await page.evaluate(() => { window.__holdHistoryDebounce = true; });
  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText("2 samples");

  await page.evaluate(() => { window.__holdHistoryDebounce = false; });
  publishProfileStep(fixture, "beta", 0);
  await waitForFixtureProfile(request, fixture, "cygnus-11", 0);
  await page.evaluate(() => { window.location.hash = "#/process/cygnus-11/0/423901"; });
  await expect(page.locator(".process-command")).toContainText("beta_train.py");
  await expect(sampleCount(page)).toHaveText("1 sample");
  const betaScope = await currentHistoryScope(page);
  expect(betaScope).not.toBe(alphaScope);
  await waitForStoredHistory(page, betaScope, 0);

  await page.evaluate(() => { window.__holdHistoryDebounce = true; });
  publishStep(fixture, 1);
  await expect(sampleCount(page)).toHaveText("2 samples");

  await page.evaluate(() => { window.__holdHistoryDebounce = false; });
  publishProfileStep(fixture, "alpha", 2);
  await waitForFixtureProfile(request, fixture, "atlas-01", 2);
  await page.evaluate(() => { window.location.hash = "#/process/atlas-01/0/423901"; });
  await expect(page.locator(".process-command")).toContainText("train.py");
  await expect(sampleCount(page)).toHaveText("3 samples");
  await expect(processMetricValue(page, "GPU util")).toHaveText("79% now | 79% peak");
  await waitForStoredHistory(page, alphaScope, 2);

  const database = await historyDatabaseSnapshot(page);
  expect(database.records).toHaveLength(2);
  expect(new Set(database.records.map((record) => record.scope))).toEqual(
    new Set([alphaScope, betaScope]),
  );
  expect(database.records.find((record) => record.scope === betaScope)?.lastTimestamp).toBe(
    FIXED_TIMESTAMP + 2,
  );
  expect(database.records.every((record) => record.ciphertextBytes > 16)).toBe(true);
  for (const plaintext of [
    "atlas-01",
    "alice",
    "train.py",
    "cygnus-11",
    "diana",
    "beta_train.py",
  ]) {
    expectNoStoredPlaintext(database, plaintext);
  }
  expect(pageErrors).toEqual([]);
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
