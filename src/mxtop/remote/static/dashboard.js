"use strict";

const app = document.getElementById("app");
const clusterSummary = document.getElementById("cluster-summary");
const connectionState = document.getElementById("connection-state");
const connectionLabel = document.getElementById("connection-label");
const sampleTime = document.getElementById("sample-time");
const refreshState = document.getElementById("refresh-state");
const appStatus = document.getElementById("app-status");
const pauseButton = document.getElementById("pause-updates");
const pauseCount = document.getElementById("pause-count");
const clearHistoryButton = document.getElementById("clear-history");
const clearHistoryDialog = document.getElementById("clear-history-dialog");
const historyStorage = window.mxtopHistoryStorage || null;
const navButtons = [...document.querySelectorAll("[data-route]")];

const state = {
  cluster: null,
  latestCluster: null,
  connected: false,
  paused: false,
  bufferedUpdates: 0,
  pausedHistory: null,
  heatMetric: "util",
  searches: { nodes: "", processes: "" },
  sorts: {
    nodes: { key: "state", direction: "ascending" },
    processes: { key: "gpuMemory", direction: "descending" },
  },
  selectedGpu: {},
  processReturnRoute: "processes",
  renderedRouteKey: null,
  announcedProcess: null,
};

// Rolling client-side history for sparklines: one sample per SSE message,
// bounded so an always-open tab cannot grow without limit.
const HISTORY_LIMIT = 240;
const PROCESS_HISTORY_LIMIT = 1024;
const PROCESS_HISTORY_RETENTION = HISTORY_LIMIT;
const PROCESS_RUNTIME_ROLLBACK_TOLERANCE = 1;
const PROCESS_CREATE_TIME_TOLERANCE = 0.01;
const PROCESS_IDENTITY_FIELDS = ["command", "user", "name", "process_type"];
const PERSISTED_HISTORY_VERSION = 1;
const PERSISTED_PROCESS_LIMIT = 256;
const PERSISTED_STRING_LIMIT = 16_384;
const HISTORY_HYDRATION_TIMEOUT_MS = 750;
const HISTORY_SAVE_DELAY_MS = 500;

function emptyHistory() {
  return {
    sequence: 0,
    lastTimestamp: null,
    timestamps: [],
    cluster: { util: [], memory: [], hostCpu: [] },
    nodes: new Map(),
    processes: new Map(),
  };
}

const history = emptyHistory();

function replaceHistory(target, source) {
  target.sequence = source.sequence;
  target.lastTimestamp = source.lastTimestamp;
  target.timestamps = source.timestamps;
  target.cluster = source.cluster;
  target.nodes = source.nodes;
  target.processes = source.processes;
}

function cloneHistory(source) {
  return {
    sequence: source.sequence,
    lastTimestamp: source.lastTimestamp,
    timestamps: [...source.timestamps],
    cluster: {
      util: [...source.cluster.util],
      memory: [...source.cluster.memory],
      hostCpu: [...source.cluster.hostCpu],
    },
    nodes: new Map([...source.nodes].map(([hostname, series]) => [
      hostname,
      { util: [...series.util], memory: [...series.memory] },
    ])),
    processes: new Map([...source.processes].map(([key, entry]) => [
      key,
      {
        ...entry,
        identity: { ...entry.identity },
        latest: { ...entry.latest },
        timestamps: [...entry.timestamps],
        cpu: [...entry.cpu],
        hostMemory: [...entry.hostMemory],
        gpuMemory: [...entry.gpuMemory],
        gpuUtil: [...entry.gpuUtil],
      },
    ])),
  };
}

function historyForRender() {
  return state.paused && state.pausedHistory ? state.pausedHistory : history;
}

const PROCESS_SNAPSHOT_FIELDS = [
  "gpu_index",
  "pid",
  "name",
  "gpu_memory_bytes",
  "user",
  "command",
  "cpu_percent",
  "host_memory_bytes",
  "runtime_seconds",
  "process_type",
  "gpu_util_percent",
  "gpu_memory_bandwidth_util_percent",
  "memory_util_percent",
  "identity",
  "create_time",
];
const PROCESS_SNAPSHOT_STRING_FIELDS = new Set([
  "name",
  "user",
  "command",
  "process_type",
  "identity",
]);

function serializeProcessEntry(key, entry, sequence) {
  const strings = [
    entry.host,
    entry.generationReason,
    entry.explicitIdentity,
    ...PROCESS_IDENTITY_FIELDS.map((field) => entry.identity[field]),
    ...[...PROCESS_SNAPSHOT_STRING_FIELDS].map((field) => entry.latest[field]),
  ];
  if (strings.some((value) => value !== null && value !== undefined
      && (typeof value !== "string" || value.length > PERSISTED_STRING_LIMIT))) {
    return null;
  }
  const serialized = {
    host: entry.host,
    gpuIndex: entry.gpuIndex,
    pid: entry.pid,
    generation: entry.generation,
    generationReason: entry.generationReason,
    generationStartedAt: entry.generationStartedAt,
    identity: Object.fromEntries(PROCESS_IDENTITY_FIELDS.map((field) => [
      field,
      entry.identity[field],
    ])),
    explicitIdentity: entry.explicitIdentity,
    createTime: entry.createTime,
    latest: Object.fromEntries(PROCESS_SNAPSHOT_FIELDS.map((field) => {
      const value = entry.latest[field];
      return [field, value === undefined ? null : value];
    })),
    reported: entry.reported,
    ended: entry.ended,
    nodeReachable: entry.nodeReachable,
    lastRuntimeSeconds: entry.lastRuntimeSeconds,
    lastSeenSequence: entry.lastSeenSequence,
    lastSeenTimestamp: entry.lastSeenTimestamp,
    timestamps: [...entry.timestamps],
    cpu: [...entry.cpu],
    hostMemory: [...entry.hostMemory],
    gpuMemory: [...entry.gpuMemory],
    gpuUtil: [...entry.gpuUtil],
  };
  return restoreProcessEntry(key, serialized, sequence) ? serialized : null;
}

function serializeHistory(source) {
  const protectedKey = currentProcessHistoryKey();
  const rankedProcesses = [...source.processes.entries()]
    .sort((left, right) => Number(right[0] === protectedKey) - Number(left[0] === protectedKey)
      || Number(right[1].reported) - Number(left[1].reported)
      || right[1].lastSeenSequence - left[1].lastSeenSequence);
  const processEntries = [];
  for (const [key, entry] of rankedProcesses) {
    const serialized = serializeProcessEntry(key, entry, source.sequence);
    if (serialized) processEntries.push([key, serialized]);
    if (processEntries.length >= PERSISTED_PROCESS_LIMIT) break;
  }
  return {
    version: PERSISTED_HISTORY_VERSION,
    sequence: source.sequence,
    lastTimestamp: source.lastTimestamp,
    timestamps: [...source.timestamps],
    cluster: {
      util: [...source.cluster.util],
      memory: [...source.cluster.memory],
      hostCpu: [...source.cluster.hostCpu],
    },
    nodes: [...source.nodes]
      .slice(0, PROCESS_HISTORY_LIMIT)
      .map(([hostname, series]) => [hostname, {
        util: [...series.util],
        memory: [...series.memory],
      }]),
    processes: processEntries,
  };
}

function storedObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function storedString(value, { empty = true } = {}) {
  return typeof value === "string"
    && value.length <= PERSISTED_STRING_LIMIT
    && (empty || value.length > 0);
}

function storedNumber(value, { integer = false, nullable = true } = {}) {
  if (value === null && nullable) return true;
  return finite(value) && (!integer || Number.isSafeInteger(value));
}

function restoreSeries(value, { monotonic = false } = {}) {
  if (!Array.isArray(value) || value.length > HISTORY_LIMIT) return null;
  let previous = null;
  const restored = [];
  for (const item of value) {
    if (item !== null && !finite(item)) return null;
    if (monotonic && finite(item) && finite(previous) && item < previous) return null;
    restored.push(item);
    if (finite(item)) previous = item;
  }
  return restored;
}

function restoreLatestProcess(value, gpuIndex, pid) {
  if (!storedObject(value)) return null;
  const restored = {};
  for (const field of PROCESS_SNAPSHOT_FIELDS) {
    const item = value[field];
    if (PROCESS_SNAPSHOT_STRING_FIELDS.has(field)) {
      if (item !== null && item !== undefined && !storedString(item)) return null;
      restored[field] = item === undefined ? null : item;
    } else {
      const integer = field === "gpu_index" || field === "pid";
      if (!storedNumber(item === undefined ? null : item, { integer })) return null;
      restored[field] = item === undefined ? null : item;
    }
  }
  if (restored.gpu_index !== gpuIndex || restored.pid !== pid) return null;
  return restored;
}

function restoreProcessEntry(key, value, sequence) {
  if (!storedString(key, { empty: false }) || !storedObject(value)) return null;
  const integerFields = ["gpuIndex", "pid", "generation", "lastSeenSequence"];
  if (!storedString(value.host, { empty: false })
      || !integerFields.every((field) => storedNumber(value[field], {
        integer: true,
        nullable: false,
      }))) return null;
  if (value.gpuIndex < 0 || value.pid < 0 || value.generation < 1
      || value.lastSeenSequence < 0 || value.lastSeenSequence > sequence
      || key !== processKey(value.host, value.gpuIndex, value.pid)) return null;
  if (!storedString(value.generationReason)
      || !storedString(value.explicitIdentity)
      || !storedObject(value.identity)
      || !PROCESS_IDENTITY_FIELDS.every((field) => storedString(value.identity[field]))) {
    return null;
  }
  const nullableNumbers = [
    "generationStartedAt",
    "createTime",
    "lastRuntimeSeconds",
    "lastSeenTimestamp",
  ];
  if (!nullableNumbers.every((field) => storedNumber(value[field]))) return null;
  if (!["reported", "ended", "nodeReachable"].every((field) => {
    return typeof value[field] === "boolean";
  })) return null;
  const timestamps = restoreSeries(value.timestamps, { monotonic: true });
  const cpu = restoreSeries(value.cpu);
  const hostMemory = restoreSeries(value.hostMemory);
  const gpuMemory = restoreSeries(value.gpuMemory);
  const gpuUtil = restoreSeries(value.gpuUtil);
  if (!timestamps || !cpu || !hostMemory || !gpuMemory || !gpuUtil
      || ![cpu, hostMemory, gpuMemory, gpuUtil].every((series) => {
        return series.length === timestamps.length;
      })) return null;
  const latest = restoreLatestProcess(value.latest, value.gpuIndex, value.pid);
  if (!latest) return null;
  return {
    host: value.host,
    gpuIndex: value.gpuIndex,
    pid: value.pid,
    generation: value.generation,
    generationReason: value.generationReason,
    generationStartedAt: value.generationStartedAt,
    identity: Object.fromEntries(PROCESS_IDENTITY_FIELDS.map((field) => {
      return [field, value.identity[field]];
    })),
    explicitIdentity: value.explicitIdentity,
    createTime: value.createTime,
    latest,
    reported: value.reported,
    ended: value.ended,
    nodeReachable: value.nodeReachable,
    lastRuntimeSeconds: value.lastRuntimeSeconds,
    lastSeenSequence: value.lastSeenSequence,
    lastSeenTimestamp: value.lastSeenTimestamp,
    timestamps,
    cpu,
    hostMemory,
    gpuMemory,
    gpuUtil,
  };
}

function restoreHistory(value, currentTimestamp, storedTimestamp) {
  if (!storedObject(value)
      || value.version !== PERSISTED_HISTORY_VERSION
      || !storedNumber(value.sequence, { integer: true, nullable: false })
      || value.sequence < 0
      || !storedNumber(value.lastTimestamp)
      || !finite(storedTimestamp)
      || value.lastTimestamp !== storedTimestamp
      || (finite(value.lastTimestamp) && finite(currentTimestamp)
        && value.lastTimestamp > currentTimestamp)
      || !storedObject(value.cluster)) return null;
  const timestamps = restoreSeries(value.timestamps, { monotonic: true });
  const util = restoreSeries(value.cluster.util);
  const memory = restoreSeries(value.cluster.memory);
  const hostCpu = restoreSeries(value.cluster.hostCpu);
  if (!timestamps || !util || !memory || !hostCpu
      || ![util, memory, hostCpu].every((series) => {
        return series.length === timestamps.length;
      })) return null;
  if (!Array.isArray(value.nodes) || value.nodes.length > PROCESS_HISTORY_LIMIT
      || !Array.isArray(value.processes)
      || value.processes.length > PERSISTED_PROCESS_LIMIT) return null;
  const nodes = new Map();
  for (const candidate of value.nodes) {
    if (!Array.isArray(candidate) || candidate.length !== 2
        || !storedString(candidate[0], { empty: false })
        || nodes.has(candidate[0]) || !storedObject(candidate[1])) return null;
    const nodeUtil = restoreSeries(candidate[1].util);
    const nodeMemory = restoreSeries(candidate[1].memory);
    if (!nodeUtil || !nodeMemory || nodeUtil.length !== nodeMemory.length
        || nodeUtil.length > timestamps.length) return null;
    nodes.set(candidate[0], { util: nodeUtil, memory: nodeMemory });
  }
  const processes = new Map();
  for (const candidate of value.processes) {
    if (!Array.isArray(candidate) || candidate.length !== 2 || processes.has(candidate[0])) {
      return null;
    }
    const entry = restoreProcessEntry(candidate[0], candidate[1], value.sequence);
    if (!entry) return null;
    processes.set(candidate[0], entry);
  }
  return {
    sequence: value.sequence,
    lastTimestamp: value.lastTimestamp,
    timestamps,
    cluster: { util, memory, hostCpu },
    nodes,
    processes,
  };
}

const historyPersistence = {
  signature: null,
  ready: false,
  hydrationEpoch: 0,
  saveEpoch: 0,
  pending: [],
  saveTimer: null,
  failureAnnounced: false,
  clearing: false,
  storageBlocked: false,
};

function clusterHistorySignature(cluster) {
  if (!cluster || !Array.isArray(cluster.nodes)) return null;
  const hosts = [];
  for (const node of cluster.nodes) {
    if (!node || typeof node.hostname !== "string") return null;
    const hostname = node.hostname.normalize("NFC");
    if (!hostname || hostname.length > 1024) return null;
    hosts.push(hostname);
  }
  return JSON.stringify([...new Set(hosts)].sort());
}

function queueHistoryCluster(cluster) {
  const timestamp = cluster.timestamp;
  const existing = historyPersistence.pending.findIndex((candidate) => {
    return finite(timestamp) && candidate.timestamp === timestamp;
  });
  if (existing >= 0) historyPersistence.pending[existing] = cluster;
  else historyPersistence.pending.push(cluster);
  if (historyPersistence.pending.length > HISTORY_LIMIT) {
    historyPersistence.pending.shift();
  }
}

function pendingHistoryTimestamp() {
  const timestamps = historyPersistence.pending.map((cluster) => cluster.timestamp)
    .filter(finite);
  return timestamps.length ? Math.max(...timestamps) : null;
}

function stopScheduledHistorySave() {
  historyPersistence.saveEpoch += 1;
  if (historyPersistence.saveTimer !== null) {
    clearTimeout(historyPersistence.saveTimer);
    historyPersistence.saveTimer = null;
  }
}

function historyLoadWithDeadline(cluster) {
  if (!historyStorage) {
    return Promise.resolve({ ok: false, status: "unavailable", payload: null });
  }
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      resolve({
        ok: false,
        status: "timeout",
        payload: null,
      });
    }, HISTORY_HYDRATION_TIMEOUT_MS);
    historyStorage.load(cluster).then((loadResult) => {
      clearTimeout(timeout);
      resolve(loadResult);
    }, () => {
      clearTimeout(timeout);
      resolve({ ok: false, status: "unavailable", payload: null });
    });
  });
}

function showHistoryLoading() {
  app.setAttribute("aria-busy", "true");
  if (appStatus) {
    appStatus.textContent = "Loading saved incident history for the current cluster.";
  }
  const loading = element("div", "loading-state");
  append(loading, element("span", "loading-line"), element("span", "", "Loading current cluster"));
  app.replaceChildren(loading);
}

function resetForHistoryScope(signature) {
  stopScheduledHistorySave();
  historyPersistence.signature = signature;
  historyPersistence.ready = false;
  historyPersistence.pending = [];
  historyPersistence.failureAnnounced = false;
  historyPersistence.clearing = false;
  historyPersistence.storageBlocked = false;
  replaceHistory(history, emptyHistory());
  state.cluster = null;
  state.latestCluster = null;
  state.paused = false;
  state.bufferedUpdates = 0;
  state.pausedHistory = null;
  state.renderedRouteKey = null;
  state.announcedProcess = null;
  clearHistoryButton.disabled = true;
  showHistoryLoading();
  updateShell();
}

async function hydrateHistoryScope(cluster, signature, epoch) {
  const loadResult = await historyLoadWithDeadline(cluster);
  if (epoch !== historyPersistence.hydrationEpoch
      || signature !== historyPersistence.signature) return;
  const currentTimestamp = pendingHistoryTimestamp();
  let restored = null;
  let invalidSavedHistory = loadResult.status === "invalid";
  if (loadResult.status === "timeout") historyPersistence.storageBlocked = true;
  if (loadResult.ok && loadResult.status === "loaded") {
    restored = restoreHistory(
      loadResult.payload,
      currentTimestamp,
      loadResult.lastTimestamp,
    );
    if (!restored) {
      invalidSavedHistory = true;
      if (historyStorage) await historyStorage.clear(cluster);
    }
  }
  if (epoch !== historyPersistence.hydrationEpoch
      || signature !== historyPersistence.signature) return;
  replaceHistory(history, restored || emptyHistory());
  historyPersistence.ready = true;
  const pending = historyPersistence.pending.splice(0)
    .sort((left, right) => {
      if (!finite(left.timestamp)) return finite(right.timestamp) ? 1 : 0;
      if (!finite(right.timestamp)) return -1;
      return left.timestamp - right.timestamp;
    });
  for (const pendingCluster of pending) applyCluster(pendingCluster);
  app.setAttribute("aria-busy", "false");
  render();
  scheduleHistorySave();
  if (appStatus && restored) {
    const samples = restored.timestamps.length;
    appStatus.textContent = `Restored ${samples} incident history sample${samples === 1 ? "" : "s"} from this browser session.`;
  } else if (appStatus && invalidSavedHistory) {
    appStatus.textContent = "Saved incident history was invalid and was not restored.";
  } else if (appStatus && loadResult.status === "timeout") {
    appStatus.textContent = "Saved incident history did not load in time. New history remains in memory for this page.";
  } else if (appStatus && loadResult.status === "unavailable") {
    appStatus.textContent = "Encrypted reload recovery is unavailable in this browser context. Incident history remains in memory.";
  } else if (appStatus && loadResult.status === "expired") {
    appStatus.textContent = "Saved incident history expired. Showing the current sample.";
  } else if (appStatus) {
    appStatus.textContent = "Current cluster loaded. No saved incident history was found.";
  }
}

function beginHistoryScope(cluster, signature) {
  if (historyPersistence.signature !== null && historyPersistence.ready) {
    void persistHistoryNow();
  }
  resetForHistoryScope(signature);
  queueHistoryCluster(cluster);
  historyPersistence.hydrationEpoch += 1;
  const epoch = historyPersistence.hydrationEpoch;
  void hydrateHistoryScope(cluster, signature, epoch);
}

function receiveCluster(cluster) {
  const signature = clusterHistorySignature(cluster);
  if (!signature) return false;
  if (signature !== historyPersistence.signature) {
    beginHistoryScope(cluster, signature);
    return true;
  }
  if (!historyPersistence.ready) {
    queueHistoryCluster(cluster);
    return true;
  }
  if (!applyCluster(cluster)) return false;
  if (state.paused) updateShell();
  else render();
  return true;
}

async function persistHistoryNow(epoch = historyPersistence.saveEpoch) {
  if (!historyStorage || !historyPersistence.ready || !state.latestCluster
      || historyPersistence.clearing || historyPersistence.storageBlocked
      || !finite(history.lastTimestamp)
      || epoch !== historyPersistence.saveEpoch) {
    return null;
  }
  if (historyPersistence.saveTimer !== null) {
    clearTimeout(historyPersistence.saveTimer);
    historyPersistence.saveTimer = null;
  }
  const result = await historyStorage.save(
    state.latestCluster,
    serializeHistory(history),
    history.lastTimestamp,
  );
  if (epoch !== historyPersistence.saveEpoch) return result;
  if (!result.ok && !historyPersistence.failureAnnounced && appStatus) {
    historyPersistence.failureAnnounced = true;
    appStatus.textContent = "Incident history remains in memory because browser storage is unavailable.";
  }
  return result;
}

function scheduleHistorySave() {
  if (!historyPersistence.ready || !state.latestCluster || historyPersistence.clearing
      || historyPersistence.storageBlocked) {
    return;
  }
  if (historyPersistence.saveTimer !== null) clearTimeout(historyPersistence.saveTimer);
  const epoch = historyPersistence.saveEpoch;
  historyPersistence.saveTimer = setTimeout(() => {
    void persistHistoryNow(epoch);
  }, HISTORY_SAVE_DELAY_MS);
}

function historySeed(cluster) {
  const seeded = emptyHistory();
  if (cluster && Array.isArray(cluster.nodes)) {
    recordHistory(cluster, seeded);
    seeded.lastTimestamp = finite(cluster.timestamp) ? cluster.timestamp : null;
  }
  return seeded;
}

async function clearIncidentHistory() {
  if (!historyPersistence.ready) return;
  const signature = historyPersistence.signature;
  historyPersistence.clearing = true;
  stopScheduledHistorySave();
  clearHistoryButton.disabled = true;
  app.setAttribute("aria-busy", "true");
  if (appStatus) appStatus.textContent = "Clearing saved incident history.";
  let clearResult = { ok: false, status: "unavailable" };
  try {
    if (historyStorage) clearResult = await historyStorage.clear();
  } catch (_) {}
  if (signature !== historyPersistence.signature) return;
  if (clearResult.ok) historyPersistence.storageBlocked = false;
  replaceHistory(history, historySeed(state.latestCluster || state.cluster));
  if (state.paused) state.pausedHistory = historySeed(state.cluster);
  historyPersistence.clearing = false;
  app.setAttribute("aria-busy", "false");
  state.announcedProcess = null;
  render();
  scheduleHistorySave();
  if (appStatus) {
    appStatus.textContent = clearResult.ok
      ? "Incident history cleared. The current sample remains."
      : "Incident history cleared from memory. Browser storage was unavailable.";
  }
}

function pushBounded(series, value) {
  series.push(finite(value) ? value : null);
  if (series.length > HISTORY_LIMIT) series.shift();
}

function processKey(host, gpuIndex, pid) {
  return JSON.stringify([String(host), Number(gpuIndex), Number(pid)]);
}

function processIdentity(process) {
  return Object.fromEntries(PROCESS_IDENTITY_FIELDS.map((field) => {
    const value = process[field];
    return [field, value === null || value === undefined ? "" : String(value)];
  }));
}

function processGenerationChangeReason(entry, process) {
  const createTime = process.create_time;
  const hasCreateTimes = finite(entry.createTime) && finite(createTime);
  if (hasCreateTimes
      && Math.abs(entry.createTime - createTime) > PROCESS_CREATE_TIME_TOLERANCE) {
    return "Process creation time changed";
  }
  const explicitIdentity = process.identity === null || process.identity === undefined
    ? ""
    : String(process.identity);
  if (entry.explicitIdentity && explicitIdentity
      && entry.explicitIdentity !== explicitIdentity) {
    return "Process identity changed";
  }
  if (hasCreateTimes) return null;
  if (entry.ended) return "PID returned after ending";
  const identity = processIdentity(process);
  const changedField = PROCESS_IDENTITY_FIELDS.find((field) => {
    return entry.identity[field] && identity[field]
      && entry.identity[field] !== identity[field];
  });
  if (changedField) {
    const label = changedField === "process_type" ? "Context" : changedField;
    return `${label[0].toUpperCase()}${label.slice(1)} changed`;
  }
  const runtime = process.runtime_seconds;
  const runtimeRolledBack = finite(entry.lastRuntimeSeconds)
    && finite(runtime)
    && runtime + PROCESS_RUNTIME_ROLLBACK_TOLERANCE < entry.lastRuntimeSeconds;
  return runtimeRolledBack ? "Runtime restarted" : null;
}

function newProcessHistory(
  node,
  process,
  previous = null,
  reason = "First observed",
  timestamp = null,
  targetHistory = history,
) {
  return {
    host: node.hostname,
    gpuIndex: Number(process.gpu_index),
    pid: Number(process.pid),
    generation: previous ? previous.generation + 1 : 1,
    generationReason: reason,
    generationStartedAt: finite(timestamp) ? timestamp : null,
    identity: processIdentity(process),
    explicitIdentity: process.identity === null || process.identity === undefined
      ? ""
      : String(process.identity),
    createTime: finite(process.create_time) ? process.create_time : null,
    latest: { ...process },
    reported: true,
    ended: false,
    nodeReachable: Boolean(node.reachable),
    lastRuntimeSeconds: finite(process.runtime_seconds) ? process.runtime_seconds : null,
    lastSeenSequence: targetHistory.sequence,
    lastSeenTimestamp: null,
    timestamps: [],
    cpu: [],
    hostMemory: [],
    gpuMemory: [],
    gpuUtil: [],
  };
}

function appendProcessSample(entry, node, process, timestamp, targetHistory = history) {
  const identity = processIdentity(process);
  for (const field of PROCESS_IDENTITY_FIELDS) {
    if (identity[field]) entry.identity[field] = identity[field];
  }
  entry.latest = { ...process };
  entry.reported = true;
  entry.ended = false;
  entry.nodeReachable = Boolean(node.reachable);
  entry.lastSeenSequence = targetHistory.sequence;
  entry.lastSeenTimestamp = finite(timestamp) ? timestamp : entry.lastSeenTimestamp;
  if (!entry.explicitIdentity && process.identity !== null && process.identity !== undefined) {
    entry.explicitIdentity = String(process.identity);
  }
  if (!finite(entry.createTime) && finite(process.create_time)) {
    entry.createTime = process.create_time;
  }
  if (finite(process.runtime_seconds)) entry.lastRuntimeSeconds = process.runtime_seconds;
  pushBounded(entry.timestamps, timestamp);
  pushBounded(entry.cpu, process.cpu_percent);
  pushBounded(entry.hostMemory, process.host_memory_bytes);
  pushBounded(entry.gpuMemory, process.gpu_memory_bytes);
  pushBounded(entry.gpuUtil, process.gpu_util_percent);
}

function currentProcessHistoryKey() {
  const route = currentRoute();
  return route.name === "process"
    ? processKey(route.host, route.gpuIndex, route.pid)
    : null;
}

function pruneProcessHistory(targetHistory = history) {
  const protectedKey = currentProcessHistoryKey();
  for (const [key, entry] of targetHistory.processes) {
    const age = targetHistory.sequence - entry.lastSeenSequence;
    if (key !== protectedKey && !entry.reported && age > PROCESS_HISTORY_RETENTION) {
      targetHistory.processes.delete(key);
    }
  }
  if (targetHistory.processes.size <= PROCESS_HISTORY_LIMIT) return;
  const candidates = [...targetHistory.processes.entries()]
    .filter(([key]) => key !== protectedKey)
    .sort((left, right) => Number(left[1].reported) - Number(right[1].reported)
      || left[1].lastSeenSequence - right[1].lastSeenSequence);
  while (targetHistory.processes.size > PROCESS_HISTORY_LIMIT && candidates.length) {
    targetHistory.processes.delete(candidates.shift()[0]);
  }
}

function recordProcessHistory(stats, timestamp, targetHistory = history) {
  targetHistory.sequence += 1;
  const seen = new Set();
  const nodeByHost = new Map(stats.nodes.map((node) => [node.hostname, node]));
  for (const node of stats.nodes) {
    if (!node.reachable) continue;
    for (const process of processesFor(node)) {
      const key = processKey(node.hostname, process.gpu_index, process.pid);
      if (seen.has(key)) continue;
      seen.add(key);
      let entry = targetHistory.processes.get(key);
      const resetReason = entry
        ? processGenerationChangeReason(entry, process)
        : "First observed";
      if (!entry || resetReason) {
        entry = newProcessHistory(
          node,
          process,
          entry,
          resetReason,
          timestamp,
          targetHistory,
        );
        targetHistory.processes.set(key, entry);
      }
      appendProcessSample(entry, node, process, timestamp, targetHistory);
    }
  }
  for (const [key, entry] of targetHistory.processes) {
    if (seen.has(key)) continue;
    const node = nodeByHost.get(entry.host);
    entry.reported = false;
    entry.nodeReachable = Boolean(node && node.reachable);
    if (node && node.reachable) entry.ended = true;
  }
  pruneProcessHistory(targetHistory);
}

function recordHistory(cluster, targetHistory = history) {
  const stats = clusterStats(cluster);
  const timestamp = cluster.timestamp;
  recordProcessHistory(stats, timestamp, targetHistory);
  if (!stats.nodeCount) return;
  pushBounded(targetHistory.timestamps, timestamp);
  pushBounded(targetHistory.cluster.util, stats.avgUtil);
  pushBounded(targetHistory.cluster.memory, stats.memory);
  pushBounded(targetHistory.cluster.hostCpu, stats.hostCpu);
  const seen = new Set();
  for (const node of stats.nodes) {
    seen.add(node.hostname);
    let series = targetHistory.nodes.get(node.hostname);
    if (!series) {
      series = { util: [], memory: [] };
      targetHistory.nodes.set(node.hostname, series);
    }
    const nodeData = nodeStats(node);
    pushBounded(series.util, node.reachable ? nodeData.util : null);
    pushBounded(series.memory, node.reachable ? nodeData.memory : null);
  }
  for (const hostname of targetHistory.nodes.keys()) {
    if (!seen.has(hostname)) targetHistory.nodes.delete(hostname);
  }
}

function applyCluster(cluster) {
  if (!cluster || !Array.isArray(cluster.nodes)) return false;
  const timestamp = cluster.timestamp;
  const stale = finite(timestamp)
    && finite(history.lastTimestamp)
    && timestamp < history.lastTimestamp;
  if (stale) return false;
  state.latestCluster = cluster;
  const replay = finite(timestamp) && timestamp === history.lastTimestamp;
  if (!replay) {
    recordHistory(cluster);
    history.lastTimestamp = finite(timestamp) ? timestamp : null;
    scheduleHistorySave();
    if (state.paused) {
      state.bufferedUpdates += 1;
      if (state.bufferedUpdates === 1 && appStatus) {
        appStatus.textContent = "New data is available while dashboard updates are paused.";
      }
    }
  }
  if (!state.paused) state.cluster = cluster;
  return true;
}

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function append(parent, ...children) {
  for (const child of children) {
    if (child) parent.append(child);
  }
  return parent;
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function devicesFor(node) {
  return node && node.frame && Array.isArray(node.frame.devices)
    ? node.frame.devices
    : [];
}

function processesFor(node) {
  return node && node.frame && Array.isArray(node.frame.processes)
    ? node.frame.processes
    : [];
}

function hostFor(node) {
  return node && node.host && typeof node.host === "object" ? node.host : {};
}

function memoryPercent(device) {
  if (finite(device.memory_util_percent)) return device.memory_util_percent;
  if (finite(device.memory_used_bytes) && finite(device.memory_total_bytes) && device.memory_total_bytes > 0) {
    return device.memory_used_bytes / device.memory_total_bytes * 100;
  }
  return null;
}

function average(values) {
  const usable = values.filter(finite);
  return usable.length ? usable.reduce((sum, value) => sum + value, 0) / usable.length : null;
}

function sumFinite(values) {
  return values.filter(finite).reduce((sum, value) => sum + value, 0);
}

function formatPercent(value) {
  return finite(value) ? `${Math.round(value)}%` : "-";
}

function formatTemperature(value) {
  return finite(value) ? `${Math.round(value)}C` : "-";
}

function formatPower(value) {
  if (!finite(value)) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} kW` : `${Math.round(value)} W`;
}

function formatBytes(value) {
  if (!finite(value)) return "-";
  if (value >= TIB) return `${(value / TIB).toFixed(2)} TiB`;
  if (value >= GIB) return `${(value / GIB).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(0)} MiB`;
  return `${Math.round(value)} B`;
}

function formatMemoryPair(used, total) {
  if (!finite(used) || !finite(total)) return "-";
  const unit = total >= TIB ? TIB : GIB;
  const suffix = unit === TIB ? "TiB" : "GiB";
  const digits = unit === TIB ? 2 : 1;
  return `${(used / unit).toFixed(digits)} / ${(total / unit).toFixed(digits)} ${suffix}`;
}

function formatDuration(seconds) {
  if (!finite(seconds)) return "-";
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor(total % 86400 / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatTimestamp(timestamp) {
  return finite(timestamp)
    ? new Date(timestamp * 1000).toLocaleTimeString()
    : "an earlier sample";
}

function formatLoad(value) {
  return finite(value) ? value.toFixed(2) : "-";
}

function level(value, metric = "util") {
  if (!finite(value)) return "unavailable";
  if (metric === "temp") {
    if (value >= 85) return "level-critical";
    if (value >= 75) return "level-high";
    if (value >= 60) return "level-mid";
    if (value >= 40) return "level-low";
    return "level-idle";
  }
  if (value >= 90) return "level-critical";
  if (value >= 75) return "level-high";
  if (value >= 40) return "level-mid";
  if (value >= 5) return "level-low";
  return "level-idle";
}

function nodeStats(node) {
  const devices = devicesFor(node);
  const processes = processesFor(node);
  const host = hostFor(node);
  const processGpus = new Set(processes.map((process) => process.gpu_index));
  const used = sumFinite(devices.map((device) => device.memory_used_bytes));
  const total = sumFinite(devices.map((device) => device.memory_total_bytes));
  const util = average(devices.map((device) => device.gpu_util_percent));
  const memory = total > 0 ? used / total * 100 : null;
  const active = devices.filter((device) => {
    return (finite(device.gpu_util_percent) && device.gpu_util_percent >= 5)
      || processGpus.has(device.index);
  }).length;
  const temperatures = devices.map((device) => device.temperature_c).filter(finite);
  const maxTemp = temperatures.length ? Math.max(...temperatures) : null;
  const power = sumFinite(devices.map((device) => device.power_w));
  const risks = devices.filter((device) => {
    const mem = memoryPercent(device);
    return (finite(device.temperature_c) && device.temperature_c >= 80)
      || (finite(mem) && mem >= 95);
  }).length
    + (node.reachable ? 0 : 1)
    + (finite(host.memory_percent) && host.memory_percent >= 95 ? 1 : 0);
  return {
    host,
    devices,
    processes,
    gpuCount: devices.length,
    active,
    util,
    used,
    total,
    memory,
    maxTemp,
    power,
    risks,
  };
}

function clusterStats(cluster = state.cluster) {
  const nodes = cluster && Array.isArray(cluster.nodes) ? cluster.nodes : [];
  const reachable = nodes.filter((node) => node.reachable);
  const devices = reachable.flatMap(devicesFor);
  const processRows = reachable.flatMap((node) => {
    return processesFor(node).map((process) => ({ node, process }));
  });
  const processKeys = new Set(processRows.map(({ node, process }) => `${node.hostname}:${process.pid}`));
  const processGpus = new Set(processRows.map(({ node, process }) => `${node.hostname}:${process.gpu_index}`));
  const used = sumFinite(devices.map((device) => device.memory_used_bytes));
  const total = sumFinite(devices.map((device) => device.memory_total_bytes));
  const active = reachable.reduce((count, node) => {
    return count + devicesFor(node).filter((device) => {
      return (finite(device.gpu_util_percent) && device.gpu_util_percent >= 5)
        || processGpus.has(`${node.hostname}:${device.index}`);
    }).length;
  }, 0);
  const risks = nodes.reduce((count, node) => count + nodeStats(node).risks, 0);
  const hostUsed = sumFinite(reachable.map((node) => hostFor(node).memory_used_bytes));
  const hostTotal = sumFinite(reachable.map((node) => hostFor(node).memory_total_bytes));
  return {
    nodes,
    reachable,
    devices,
    processRows,
    nodeCount: nodes.length,
    onlineCount: reachable.length,
    gpuCount: devices.length,
    active,
    avgUtil: average(devices.map((device) => device.gpu_util_percent)),
    used,
    total,
    memory: total > 0 ? used / total * 100 : null,
    power: sumFinite(devices.map((device) => device.power_w)),
    hostCpu: average(reachable.map((node) => hostFor(node).cpu_percent)),
    hostUsed,
    hostTotal,
    hostMemory: hostTotal > 0 ? hostUsed / hostTotal * 100 : null,
    processCount: processKeys.size,
    risks,
  };
}

function currentRoute() {
  const raw = window.location.hash.startsWith("#/")
    ? window.location.hash.slice(2)
    : "overview";
  const parts = raw.split("/");
  if (parts[0] === "process" && parts.length === 4) {
    try {
      const host = decodeURIComponent(parts[1]);
      const gpuIndex = Number(decodeURIComponent(parts[2]));
      const pid = Number(decodeURIComponent(parts[3]));
      if (host && Number.isInteger(gpuIndex) && gpuIndex >= 0
          && Number.isInteger(pid) && pid >= 0) {
        return { name: "process", host, gpuIndex, pid };
      }
    } catch (_) {
      return { name: "processes" };
    }
    return { name: "processes" };
  }
  if (parts[0] === "node" && parts.length > 1) {
    try {
      return { name: "node", host: decodeURIComponent(parts.slice(1).join("/")) };
    } catch (_) {
      return { name: "nodes" };
    }
  }
  return ["overview", "nodes", "processes"].includes(parts[0])
    ? { name: parts[0] }
    : { name: "overview" };
}

function routeKey(route = currentRoute()) {
  return JSON.stringify(route);
}

function navigate(route) {
  window.location.hash = `#/${route}`;
}

function navigateNode(host, gpuIndex = null) {
  if (gpuIndex !== null) state.selectedGpu[host] = gpuIndex;
  navigate(`node/${encodeURIComponent(host)}`);
}

function rememberProcessReturnRoute() {
  const source = currentRoute();
  if (source.name === "node") {
    state.processReturnRoute = `node/${encodeURIComponent(source.host)}`;
  } else if (source.name !== "process") {
    state.processReturnRoute = "processes";
  }
}

function processRoute(host, gpuIndex, pid) {
  return [
    "process",
    encodeURIComponent(host),
    encodeURIComponent(String(gpuIndex)),
    encodeURIComponent(String(pid)),
  ].join("/");
}

function processHref(host, gpuIndex, pid) {
  return `#/${processRoute(host, gpuIndex, pid)}`;
}

function navigateProcess(host, gpuIndex, pid) {
  rememberProcessReturnRoute();
  navigate(processRoute(host, gpuIndex, pid));
}

function stableFocusKey(...parts) {
  return JSON.stringify(parts.map((part) => String(part)));
}

function keepFocus(node, ...parts) {
  node.dataset.focusKey = stableFocusKey(...parts);
  return node;
}

function captureTransientState() {
  const active = document.activeElement;
  let focus = null;
  if (active instanceof HTMLElement && active.dataset.focusKey) {
    const textControl = active instanceof HTMLInputElement
      || active instanceof HTMLTextAreaElement;
    focus = {
      key: active.dataset.focusKey,
      start: textControl ? active.selectionStart : null,
      end: textControl ? active.selectionEnd : null,
    };
  }
  const scroll = {};
  document.querySelectorAll("[data-scroll-key]").forEach((node) => {
    scroll[node.dataset.scrollKey] = node.scrollLeft;
  });
  return { focus, scroll };
}

function restoreTransientState(transient) {
  for (const [key, left] of Object.entries(transient.scroll)) {
    const node = [...document.querySelectorAll("[data-scroll-key]")]
      .find((candidate) => candidate.dataset.scrollKey === key);
    if (node) node.scrollLeft = left;
  }
  if (!transient.focus) return;
  const target = [...document.querySelectorAll("[data-focus-key]")]
    .find((node) => node.dataset.focusKey === transient.focus.key);
  if (!(target instanceof HTMLElement)) return;
  target.focus({ preventScroll: true });
  const textControl = target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement;
  if (textControl && transient.focus.start !== null && transient.focus.end !== null) {
    target.setSelectionRange(transient.focus.start, transient.focus.end);
  }
}

function pageHead(title, meta = "", actions = null, back = false) {
  const head = element("div", "page-head");
  const titleWrap = element("div", back ? "title-with-back" : "page-title-wrap");
  if (back) {
    const backOptions = back === true
      ? { route: "nodes", label: "Back to nodes" }
      : back;
    const button = element("button", "back-button");
    button.type = "button";
    button.title = backOptions.label;
    button.setAttribute("aria-label", backOptions.label);
    keepFocus(button, "back", backOptions.route);
    button.addEventListener("click", () => navigate(backOptions.route));
    titleWrap.append(button);
  }
  const labels = element("div", "page-title-wrap");
  const heading = element("h1", "page-title", title);
  heading.tabIndex = -1;
  keepFocus(heading, "page-title");
  append(labels, heading);
  if (meta) append(labels, element("div", "page-meta", meta));
  titleWrap.append(labels);
  head.append(titleWrap);
  if (actions) {
    const actionWrap = element("div", "page-actions");
    actionWrap.append(actions);
    head.append(actionWrap);
  }
  return head;
}

function kpiStrip(items) {
  const strip = element("section", "kpi-strip");
  strip.setAttribute("aria-label", "Summary metrics");
  strip.style.setProperty("--kpi-count", items.length);
  for (const item of items) {
    const kpi = element("div", "kpi");
    const value = element("span", `kpi-value ${item.tone || ""}`.trim(), item.value);
    if (item.title) value.title = item.title;
    append(kpi, element("span", "kpi-label", item.label), value);
    strip.append(kpi);
  }
  return strip;
}

function section(title, content, right = null, className = "") {
  const block = element("section", `dashboard-section ${className}`.trim());
  const head = element("div", "section-head");
  append(head, element("h2", "section-title", title), right);
  append(block, head, content);
  return block;
}

function segmented(options, selected, onSelect, label) {
  const control = element("div", "segmented");
  control.setAttribute("role", "group");
  control.setAttribute("aria-label", label);
  for (const option of options) {
    const button = element("button", option.value === selected ? "active" : "", option.label);
    button.type = "button";
    keepFocus(button, "segmented", label, option.value);
    button.setAttribute("aria-pressed", option.value === selected ? "true" : "false");
    button.addEventListener("click", () => onSelect(option.value));
    control.append(button);
  }
  return control;
}

function metricBar(value, metric = "util", label = null) {
  const wrapper = element("span", "metric-inline");
  const track = element("span", "metric-bar");
  const fill = element("span", level(value, metric));
  fill.style.width = finite(value) ? `${Math.max(0, Math.min(100, value))}%` : "0%";
  track.append(fill);
  append(wrapper, track, element("span", "", label === null ? formatPercent(value) : label));
  return wrapper;
}

const NODE_TABLE_COLUMNS = [
  {
    key: "node",
    label: "Node",
    left: true,
    kind: "text",
    defaultDirection: "ascending",
    value: (node) => node.hostname,
  },
  {
    key: "state",
    label: "State",
    left: true,
    kind: "number",
    defaultDirection: "ascending",
    value: (node) => Number(Boolean(node.reachable)),
  },
  {
    key: "gpus",
    label: "GPUs",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).gpuCount,
  },
  {
    key: "active",
    label: "Active",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).active,
  },
  {
    key: "util",
    label: "Util",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).util,
  },
  {
    key: "memory",
    label: "HBM",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).memory,
  },
  {
    key: "temperature",
    label: "Peak temp",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).maxTemp,
  },
  {
    key: "power",
    label: "Power",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).power,
  },
  {
    key: "cpu",
    label: "CPU",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).host.cpu_percent,
  },
  {
    key: "ram",
    label: "RAM",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).host.memory_percent,
  },
  {
    key: "load",
    label: "Load",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).host.load_average_1m,
  },
  {
    key: "processes",
    label: "Procs",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => nodeStats(node).processes.length,
  },
  {
    key: "latency",
    label: "SSH",
    kind: "number",
    defaultDirection: "descending",
    value: (node) => node.latency_ms,
  },
];

const PROCESS_TABLE_COLUMNS = [
  {
    key: "node",
    label: "Node",
    left: true,
    kind: "text",
    defaultDirection: "ascending",
    value: ({ node }) => node.hostname,
  },
  {
    key: "gpu",
    label: "GPU",
    kind: "number",
    defaultDirection: "ascending",
    value: ({ process }) => process.gpu_index,
  },
  {
    key: "pid",
    label: "PID",
    kind: "number",
    defaultDirection: "ascending",
    value: ({ process }) => process.pid,
  },
  {
    key: "type",
    label: "Type",
    left: true,
    kind: "text",
    defaultDirection: "ascending",
    value: ({ process }) => process.process_type,
  },
  {
    key: "user",
    label: "User",
    left: true,
    kind: "text",
    defaultDirection: "ascending",
    value: ({ process }) => process.user,
  },
  {
    key: "gpuMemory",
    label: "GPU memory",
    kind: "number",
    defaultDirection: "descending",
    value: ({ process }) => process.gpu_memory_bytes,
  },
  {
    key: "gpuUtil",
    label: "GPU util",
    kind: "number",
    defaultDirection: "descending",
    value: ({ process }) => process.gpu_util_percent,
  },
  {
    key: "cpu",
    label: "CPU",
    kind: "number",
    defaultDirection: "descending",
    value: ({ process }) => process.cpu_percent,
  },
  {
    key: "hostMemory",
    label: "Host memory",
    kind: "number",
    defaultDirection: "descending",
    value: ({ process }) => process.host_memory_bytes,
  },
  {
    key: "runtime",
    label: "Runtime",
    kind: "number",
    defaultDirection: "descending",
    value: ({ process }) => process.runtime_seconds,
  },
  {
    key: "command",
    label: "Command",
    left: true,
    kind: "text",
    defaultDirection: "ascending",
    value: ({ process }) => process.command || process.name,
  },
];

function missingSortValue(value, kind) {
  if (kind === "number") return !finite(value);
  return value === null || value === undefined || String(value).trim() === "";
}

function compareText(left, right) {
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: "base",
  }) || String(left).localeCompare(String(right));
}

function compareSortValues(left, right, column, direction) {
  const leftMissing = missingSortValue(left, column.kind);
  const rightMissing = missingSortValue(right, column.kind);
  if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
  if (leftMissing) return 0;
  const comparison = column.kind === "number"
    ? left - right
    : compareText(left, right);
  return direction === "descending" ? -comparison : comparison;
}

function compareIdentityNumber(left, right) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const normalizedLeft = finite(leftNumber) ? leftNumber : Number.MAX_SAFE_INTEGER;
  const normalizedRight = finite(rightNumber) ? rightNumber : Number.MAX_SAFE_INTEGER;
  return normalizedLeft - normalizedRight;
}

function nodeTieBreaker(left, right) {
  return compareText(left.hostname, right.hostname);
}

function processTieBreaker(left, right) {
  return compareText(left.node.hostname, right.node.hostname)
    || compareIdentityNumber(left.process.gpu_index, right.process.gpu_index)
    || compareIdentityNumber(left.process.pid, right.process.pid);
}

function sortedTableRows(scope, rows, columns, tieBreaker) {
  const sort = state.sorts[scope];
  const column = columns.find((candidate) => candidate.key === sort.key) || columns[0];
  const values = new Map(rows.map((row) => [row, column.value(row)]));
  return [...rows].sort((left, right) => {
    return compareSortValues(
      values.get(left),
      values.get(right),
      column,
      sort.direction,
    ) || tieBreaker(left, right);
  });
}

function updateSort(scope, column) {
  const current = state.sorts[scope];
  const direction = current.key === column.key
    ? current.direction === "ascending" ? "descending" : "ascending"
    : column.defaultDirection;
  state.sorts[scope] = { key: column.key, direction };
  if (appStatus) {
    const semanticOrder = column.key === "state"
      ? `, ${direction === "ascending" ? "Down first" : "Online first"}`
      : "";
    appStatus.textContent = `${scope === "nodes" ? "Nodes" : "Processes"} sorted by ${column.label}, ${direction}${semanticOrder}.`;
  }
  render();
}

function tableShell(
  headers,
  scrollKey,
  { sortScope = null, label = null } = {},
) {
  const wrap = element("div", "table-wrap");
  wrap.dataset.scrollKey = scrollKey;
  const table = document.createElement("table");
  if (label) {
    table.setAttribute("aria-label", label);
    wrap.dataset.tableLabel = label;
    wrap.tabIndex = -1;
    keepFocus(wrap, "table-scroll", scrollKey);
    wrap.addEventListener("blur", syncTableScrollRegions);
  }
  const thead = document.createElement("thead");
  const row = document.createElement("tr");
  for (const header of headers) {
    const th = element("th", header.left ? "left" : "");
    th.scope = "col";
    if (sortScope && header.key) {
      const sort = state.sorts[sortScope];
      const active = sort.key === header.key;
      const direction = active ? sort.direction : "none";
      th.classList.add("sortable-header");
      if (active) th.setAttribute("aria-sort", direction);
      th.dataset.sortDirection = direction;
      const button = element("button", "sort-button", header.label);
      button.type = "button";
      button.dataset.sortDirection = direction;
      keepFocus(button, "sort", sortScope, header.key);
      const nextDirection = active
        ? direction === "ascending" ? "descending" : "ascending"
        : header.defaultDirection;
      const currentOrder = header.key === "state"
        ? `, ${direction === "ascending" ? "Down first" : "Online first"}`
        : "";
      const nextOrder = header.key === "state"
        ? `, ${nextDirection === "ascending" ? "Down first" : "Online first"}`
        : "";
      button.setAttribute(
        "aria-label",
        active
          ? `${header.label}, sorted ${direction}${currentOrder}; sort ${nextDirection}${nextOrder}`
          : `${header.label}, sort ${nextDirection}${nextOrder}`,
      );
      button.addEventListener("click", () => updateSort(sortScope, header));
      th.append(button);
    } else {
      th.textContent = header.label;
    }
    row.append(th);
  }
  thead.append(row);
  const tbody = document.createElement("tbody");
  append(table, thead, tbody);
  wrap.append(table);
  return { wrap, table, tbody };
}

function syncTableScrollRegions() {
  document.querySelectorAll(".table-wrap[data-table-label]").forEach((wrap) => {
    const scrollable = wrap.scrollWidth > wrap.clientWidth + 1;
    const focused = document.activeElement === wrap;
    if (scrollable || focused) {
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "region");
      wrap.setAttribute(
        "aria-label",
        scrollable
          ? `${wrap.dataset.tableLabel} table, horizontally scrollable`
          : `${wrap.dataset.tableLabel} table`,
      );
    } else {
      wrap.tabIndex = -1;
      wrap.removeAttribute("role");
      wrap.removeAttribute("aria-label");
    }
  });
}

function cell(row, content, className = "") {
  const td = element("td", className);
  if (content instanceof Node) td.append(content);
  else td.textContent = content === null || content === undefined ? "-" : String(content);
  row.append(td);
  return td;
}

function nodeState(node) {
  const view = element("span", `node-state ${node.reachable ? "online" : "offline"}`);
  append(view, element("span", "status-dot"), element("span", "", node.reachable ? "Online" : "Down"));
  if (!node.reachable && node.error) view.title = node.error;
  return view;
}

function makeClickableRow(row, host) {
  row.classList.add("clickable");
  row.tabIndex = 0;
  keepFocus(row, "node-row", host);
  row.setAttribute("role", "link");
  row.setAttribute("aria-label", `Open ${host}`);
  row.addEventListener("click", () => navigateNode(host));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      navigateNode(host);
    }
  });
}

function clusterKpis(stats) {
  const nodeTone = stats.onlineCount === stats.nodeCount ? "good" : "critical";
  const riskTone = stats.risks ? "critical" : "good";
  return kpiStrip([
    { label: "Nodes", value: `${stats.onlineCount} / ${stats.nodeCount}`, tone: nodeTone },
    { label: "GPUs", value: stats.gpuCount },
    { label: "Active", value: `${stats.active} / ${stats.gpuCount}`, title: "GPU utilization >= 5% or a reported GPU process" },
    { label: "Avg util", value: formatPercent(stats.avgUtil) },
    { label: "HBM", value: formatMemoryPair(stats.used, stats.total), tone: finite(stats.memory) && stats.memory >= 95 ? "critical" : "" },
    { label: "Host CPU", value: formatPercent(stats.hostCpu) },
    { label: "Host RAM", value: formatMemoryPair(stats.hostUsed, stats.hostTotal), tone: finite(stats.hostMemory) && stats.hostMemory >= 95 ? "critical" : "" },
    { label: "Power", value: formatPower(stats.power) },
    { label: "Risks", value: stats.risks, tone: riskTone, title: "Offline nodes, temperature >= 80C, HBM >= 95%, or host RAM >= 95%" },
  ]);
}

function heatValue(device, metric) {
  if (!device) return null;
  if (metric === "mem") return memoryPercent(device);
  if (metric === "temp") return device.temperature_c;
  return device.gpu_util_percent;
}

function heatLabel(value, metric) {
  if (!finite(value)) return "-";
  return metric === "temp" ? `${Math.round(value)}` : `${Math.round(value)}%`;
}

function renderHeatmap(stats) {
  const scroll = element("div", "heatmap-scroll");
  scroll.dataset.scrollKey = "overview-heatmap";
  const map = element("div", "heatmap");
  const maxGpuCount = Math.max(1, ...stats.nodes.map((node) => devicesFor(node).length));
  const header = element("div", "heatmap-row");
  header.style.setProperty("--gpu-count", maxGpuCount);
  header.style.setProperty("--heatmap-min-width", `${84 + maxGpuCount * 31}px`);
  header.append(element("span", "heatmap-index", "Node / GPU"));
  for (let index = 0; index < maxGpuCount; index += 1) {
    header.append(element("span", "heatmap-index", index));
  }
  map.append(header);

  for (const node of stats.nodes) {
    const row = element("div", "heatmap-row");
    row.style.setProperty("--gpu-count", maxGpuCount);
    row.style.setProperty("--heatmap-min-width", `${84 + maxGpuCount * 31}px`);
    const label = element("button", "heatmap-label", node.hostname);
    label.type = "button";
    label.title = `Open ${node.hostname}`;
    keepFocus(label, "heatmap-node", node.hostname);
    if (!node.reachable) label.classList.add("critical");
    label.addEventListener("click", () => navigateNode(node.hostname));
    row.append(label);
    const byIndex = new Map(devicesFor(node).map((device) => [device.index, device]));
    for (let index = 0; index < maxGpuCount; index += 1) {
      const device = byIndex.get(index);
      const value = node.reachable ? heatValue(device, state.heatMetric) : null;
      const button = element(
        "button",
        `heat-cell ${node.reachable ? level(value, state.heatMetric) : "unavailable"}`,
        heatLabel(value, state.heatMetric),
      );
      button.type = "button";
      keepFocus(button, "heatmap-gpu", node.hostname, index);
      button.title = device
        ? `${node.hostname} GPU ${index}: ${state.heatMetric} ${heatLabel(value, state.heatMetric)}`
        : `${node.hostname} GPU ${index}: unavailable`;
      button.addEventListener("click", () => navigateNode(node.hostname, index));
      row.append(button);
    }
    map.append(row);
  }
  scroll.append(map);
  return scroll;
}

function renderNodeTable(nodes, scrollKey) {
  const shell = tableShell(NODE_TABLE_COLUMNS, scrollKey, {
    sortScope: "nodes",
    label: "Nodes",
  });
  const sortedNodes = sortedTableRows(
    "nodes",
    nodes,
    NODE_TABLE_COLUMNS,
    nodeTieBreaker,
  );
  for (const node of sortedNodes) {
    const stats = nodeStats(node);
    const row = document.createElement("tr");
    makeClickableRow(row, node.hostname);
    cell(row, node.hostname, "left node-name");
    cell(row, nodeState(node), "left");
    cell(row, stats.gpuCount);
    cell(row, `${stats.active} / ${stats.gpuCount}`);
    cell(row, metricBar(stats.util));
    cell(row, metricBar(stats.memory, "mem", formatMemoryPair(stats.used, stats.total)));
    const tempClass = finite(stats.maxTemp) && stats.maxTemp >= 80 ? "critical" : "";
    cell(row, formatTemperature(stats.maxTemp), tempClass);
    cell(row, formatPower(stats.power));
    cell(row, formatPercent(stats.host.cpu_percent));
    cell(row, metricBar(stats.host.memory_percent, "mem", formatMemoryPair(stats.host.memory_used_bytes, stats.host.memory_total_bytes)));
    cell(row, formatLoad(stats.host.load_average_1m));
    cell(row, stats.processes.length);
    cell(row, finite(node.latency_ms) ? `${Math.round(node.latency_ms)} ms` : "-");
    shell.tbody.append(row);
  }
  if (!nodes.length) {
    const row = document.createElement("tr");
    const empty = cell(row, "No matching nodes", "table-empty");
    empty.colSpan = 13;
    shell.tbody.append(row);
  }
  return shell.wrap;
}

function deviceSeverity(device) {
  const util = finite(device.gpu_util_percent) ? device.gpu_util_percent : 0;
  const mem = finite(memoryPercent(device)) ? memoryPercent(device) : 0;
  const temp = finite(device.temperature_c) ? Math.max(0, (device.temperature_c - 35) * 2) : 0;
  return Math.max(util, mem, temp);
}

function renderHotspots(stats) {
  const list = element("ul", "hotspot-list");
  const items = [];
  for (const node of stats.nodes.filter((candidate) => !candidate.reachable)) {
    items.push({ node, down: true, score: 1000 });
  }
  for (const node of stats.reachable) {
    for (const device of devicesFor(node)) {
      items.push({ node, device, down: false, score: deviceSeverity(device) });
    }
  }
  items.sort((left, right) => right.score - left.score);
  for (const item of items.slice(0, 8)) {
    const row = element("li", "hotspot-item");
    const target = element(
      "button",
      "hotspot-target",
      item.down ? item.node.hostname : `${item.node.hostname} / GPU ${item.device.index}`,
    );
    target.type = "button";
    keepFocus(
      target,
      "hotspot",
      item.node.hostname,
      item.device ? item.device.index : "node",
    );
    target.addEventListener("click", () => navigateNode(item.node.hostname, item.device ? item.device.index : null));
    if (item.down) {
      const down = element("span", "hotspot-metric critical", "DOWN");
      if (item.node.error) down.title = item.node.error;
      append(row, target, down);
    } else {
      append(
        row,
        target,
        element("span", "hotspot-metric util", formatPercent(item.device.gpu_util_percent)),
        element("span", "hotspot-metric", formatPercent(memoryPercent(item.device))),
        element("span", `hotspot-metric ${finite(item.device.temperature_c) && item.device.temperature_c >= 80 ? "critical" : ""}`, formatTemperature(item.device.temperature_c)),
      );
    }
    list.append(row);
  }
  if (!items.length) list.append(element("li", "empty-state", "No GPU data"));
  return list;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function sparkline(values, options = {}) {
  const width = options.width || 220;
  const height = options.height || 44;
  const max = finite(options.max) ? options.max : Math.max(1, ...values.filter(finite));
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.classList.add("sparkline");
  if (options.label) svg.setAttribute("aria-label", options.label);
  svg.setAttribute("role", "img");
  const points = values.length;
  const y = (value) => height - 1 - Math.max(0, Math.min(1, value / max)) * (height - 2);
  if (points === 1) {
    if (finite(values[0])) {
      const point = document.createElementNS(SVG_NS, "circle");
      point.setAttribute("cx", String(width / 2));
      point.setAttribute("cy", y(values[0]).toFixed(1));
      point.setAttribute("r", "2.5");
      point.setAttribute("fill", "var(--accent)");
      point.classList.add("sparkline-point");
      svg.append(point);
    }
    return svg;
  }
  if (points < 2) return svg;
  const step = width / (points - 1);
  let linePath = "";
  let areaPath = "";
  let open = false;
  for (let index = 0; index < points; index += 1) {
    const value = values[index];
    const x = index * step;
    if (!finite(value)) {
      if (open && areaPath) areaPath += ` V ${height} Z`;
      open = false;
      continue;
    }
    if (!open) {
      linePath += ` M ${x.toFixed(1)} ${y(value).toFixed(1)}`;
      areaPath += ` M ${x.toFixed(1)} ${height} L ${x.toFixed(1)} ${y(value).toFixed(1)}`;
      open = true;
    } else {
      linePath += ` L ${x.toFixed(1)} ${y(value).toFixed(1)}`;
      areaPath += ` L ${x.toFixed(1)} ${y(value).toFixed(1)}`;
    }
  }
  if (open && areaPath) areaPath += ` V ${height} Z`;
  const area = document.createElementNS(SVG_NS, "path");
  area.setAttribute("d", areaPath.trim());
  area.classList.add("sparkline-area");
  const line = document.createElementNS(SVG_NS, "path");
  line.setAttribute("d", linePath.trim());
  line.classList.add("sparkline-line");
  svg.append(area, line);
  return svg;
}

function trendCard(title, values, formatValue, options = {}) {
  const card = element("div", "trend-card");
  const latest = [...values].reverse().find(finite);
  const head = element("div", "trend-head");
  append(
    head,
    element("span", "trend-title", title),
    element("span", "trend-value", finite(latest) ? formatValue(latest) : "-"),
  );
  append(card, head, sparkline(values, { ...options, label: `${title} history` }));
  return card;
}

function processMetric(title, values, formatValue, options = {}) {
  const metric = element("figure", "process-metric");
  const usable = values.filter(finite);
  const latest = [...usable].reverse()[0];
  const peak = usable.length ? Math.max(...usable) : null;
  const caption = element("figcaption", "trend-head");
  append(
    caption,
    element("span", "trend-title", title),
    element(
      "span",
      "trend-value",
      finite(latest)
        ? `${formatValue(latest)} now | ${formatValue(peak)} peak`
        : "-",
    ),
  );
  append(metric, caption, sparkline(values, { ...options, label: `${title} history` }));
  return metric;
}

function renderTrends(renderHistory) {
  const strip = element("div", "trend-strip");
  append(
    strip,
    trendCard("Avg GPU util", renderHistory.cluster.util, formatPercent, { max: 100 }),
    trendCard("HBM used", renderHistory.cluster.memory, formatPercent, { max: 100 }),
    trendCard("Host CPU", renderHistory.cluster.hostCpu, formatPercent, { max: 100 }),
  );
  return strip;
}

function renderOverview() {
  const stats = clusterStats();
  const renderHistory = historyForRender();
  const fragment = document.createDocumentFragment();
  const controls = segmented([
    { value: "util", label: "Util" },
    { value: "mem", label: "HBM" },
    { value: "temp", label: "Temp" },
  ], state.heatMetric, (metric) => {
    state.heatMetric = metric;
    render();
  }, "Heatmap metric");
  append(
    fragment,
    pageHead("Fleet overview", `${stats.processCount} unique GPU processes`),
    clusterKpis(stats),
  );
  if (renderHistory.timestamps.length >= 2) {
    fragment.append(
      section(
        "Trends",
        renderTrends(renderHistory),
        element("span", "section-count", `${renderHistory.timestamps.length} samples`),
      ),
    );
  }
  fragment.append(section("GPU matrix", renderHeatmap(stats), controls));
  const lower = element("div", "overview-lower");
  append(
    lower,
    section("Node health", renderNodeTable(stats.nodes, "overview-nodes"), element("span", "section-count", `${stats.nodes.length} nodes`)),
    section("Hotspots", renderHotspots(stats), element("span", "section-count", "util | HBM | temp")),
  );
  fragment.append(lower);
  return fragment;
}

function searchToolbar(view, count, placeholder) {
  const toolbar = element("div", "toolbar");
  const input = element("input", "search-box");
  input.type = "search";
  input.placeholder = placeholder;
  input.value = state.searches[view];
  input.dataset.focusKey = `${view}-search`;
  input.setAttribute("aria-label", placeholder);
  input.addEventListener("input", (event) => {
    state.searches[view] = event.target.value;
    render();
  });
  append(toolbar, input, element("span", "result-count", `${count} rows`));
  return toolbar;
}

function renderNodes() {
  const stats = clusterStats();
  const query = state.searches.nodes.trim().toLowerCase();
  const nodes = stats.nodes
    .filter((node) => {
      const device = devicesFor(node)[0] || {};
      return !query || [node.hostname, device.name, device.driver_version, device.maca_version]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
  const fragment = document.createDocumentFragment();
  append(
    fragment,
    pageHead("Nodes", `${stats.onlineCount} online | ${stats.nodeCount - stats.onlineCount} down`),
    searchToolbar("nodes", nodes.length, "Search node, model, or version"),
    renderNodeTable(nodes, "nodes-table"),
  );
  return fragment;
}

function allProcessRows() {
  const rows = [];
  for (const node of clusterStats().nodes) {
    for (const process of processesFor(node)) rows.push({ node, process });
  }
  return rows;
}

function findCurrentProcess(host, gpuIndex, pid) {
  const node = clusterStats().nodes.find((candidate) => candidate.hostname === host);
  const process = node && node.reachable
    ? processesFor(node).find((candidate) => {
      return Number(candidate.gpu_index) === gpuIndex && Number(candidate.pid) === pid;
    })
    : null;
  return { node, process };
}

function processHistoryFor(host, gpuIndex, pid) {
  const key = processKey(host, gpuIndex, pid);
  const renderHistory = historyForRender();
  let entry = renderHistory.processes.get(key);
  const current = findCurrentProcess(host, gpuIndex, pid);
  const resetReason = entry && current.process
    ? processGenerationChangeReason(entry, current.process)
    : null;
  if (current.process && (!entry || resetReason)) {
    entry = newProcessHistory(
      current.node,
      current.process,
      entry,
      resetReason || "First observed",
      state.cluster.timestamp,
      renderHistory,
    );
    appendProcessSample(
      entry,
      current.node,
      current.process,
      state.cluster.timestamp,
      renderHistory,
    );
    renderHistory.processes.set(key, entry);
    if (renderHistory === history) pruneProcessHistory();
  }
  return { entry, ...current };
}

function processStatus(entry, node) {
  const lastSeen = formatTimestamp(entry.lastSeenTimestamp);
  if (node && !node.reachable) {
    return {
      label: "Node down",
      tone: "critical",
      className: "node-down",
      notice: `Node unreachable. Showing the last process sample from ${lastSeen}.`,
    };
  }
  if (!node) {
    return {
      label: "Not reported",
      tone: "warn",
      className: "ended",
      notice: `Process is not currently reported. Last seen at ${lastSeen}.`,
    };
  }
  if (entry.reported) {
    return {
      label: "Live",
      tone: "good",
      className: "live",
      notice: "",
    };
  }
  if (entry.ended) {
    return {
      label: "Ended",
      tone: "warn",
      className: "ended",
      notice: `Process no longer reported. Last seen at ${lastSeen}.`,
    };
  }
  return { label: "Not reported", tone: "warn", className: "ended", notice: "" };
}

function announceProcessTransition(entry, status) {
  const key = processKey(entry.host, entry.gpuIndex, entry.pid);
  const previous = state.announcedProcess;
  state.announcedProcess = {
    key,
    status: status.className,
    generation: entry.generation,
  };
  if (!appStatus || !previous || previous.key !== key) return;
  if (previous.generation !== entry.generation) {
    appStatus.textContent = `Process ${entry.pid} started a new generation.`;
  } else if (previous.status !== status.className) {
    appStatus.textContent = `Process ${entry.pid} status changed to ${status.label}.`;
  }
}

function renderProcessDetail(host, gpuIndex, pid) {
  const detail = element("article", "process-detail");
  const { entry, node } = processHistoryFor(host, gpuIndex, pid);
  const nodeButton = element("button", "link-button", `${host} / GPU ${gpuIndex}`);
  nodeButton.type = "button";
  nodeButton.setAttribute("aria-label", `Open ${host} GPU ${gpuIndex}`);
  keepFocus(nodeButton, "process-detail-node", host, gpuIndex, pid);
  nodeButton.addEventListener("click", () => navigateNode(host, gpuIndex));
  const back = {
    route: state.processReturnRoute || "processes",
    label: state.processReturnRoute.startsWith("node/")
      ? "Back to node"
      : "Back to processes",
  };

  if (!entry) {
    append(
      detail,
      pageHead(
        "Process not found",
        `${host} | GPU ${gpuIndex} | PID ${pid}`,
        nodeButton,
        back,
      ),
      element(
        "div",
        "empty-state",
        `No matching process or retained history is available for ${host} / GPU ${gpuIndex} / PID ${pid}.`,
      ),
    );
    return detail;
  }

  const process = entry.latest;
  const status = processStatus(entry, node);
  const name = process.name || entry.identity.name || "GPU process";
  const command = process.command || entry.identity.command || name;
  const meta = [
    name,
    host,
    `GPU ${gpuIndex}`,
    process.process_type || entry.identity.process_type || "type unavailable",
  ].join(" | ");
  const titleActions = element("div", "process-title-actions");
  append(
    titleActions,
    element(
      "span",
      `process-state ${status.className}`,
      status.label,
    ),
    nodeButton,
  );
  const title = pageHead(`PID ${pid}`, meta, titleActions, back);
  title.classList.add("process-title-row");
  append(detail, title);
  if (status.notice) {
    detail.append(element(
      "div",
      `process-state-band ${status.className}`,
      status.notice,
    ));
  }
  const generationStarted = formatTimestamp(entry.generationStartedAt);
  detail.append(element(
    "div",
    "process-generation page-meta",
    entry.generation > 1
      ? `${entry.generationReason}. History restarted at ${generationStarted}.`
      : `Generation 1 first observed at ${generationStarted}.`,
  ));
  announceProcessTransition(entry, status);
  const summary = kpiStrip([
    { label: "State", value: status.label, tone: status.tone },
    {
      label: "Generation",
      value: entry.generation,
      title: `${entry.generationReason} at ${generationStarted}`,
    },
    { label: "User", value: process.user || entry.identity.user || "-" },
    { label: "Context", value: process.process_type || entry.identity.process_type || "-" },
    { label: "Runtime", value: formatDuration(process.runtime_seconds) },
    { label: "GPU util", value: formatPercent(process.gpu_util_percent) },
    {
      label: "Mem BW",
      value: formatPercent(process.gpu_memory_bandwidth_util_percent),
    },
    { label: "GPU memory", value: formatBytes(process.gpu_memory_bytes) },
    { label: "CPU", value: formatPercent(process.cpu_percent) },
    { label: "Host memory", value: formatBytes(process.host_memory_bytes) },
  ]);
  summary.classList.add("process-summary");
  summary.querySelectorAll(".kpi").forEach((item) => {
    item.classList.add("process-summary-item");
  });
  detail.append(summary);

  const commandBlock = element("code", "process-command page-meta", command);
  const copyButton = element("button", "link-button process-copy", "Copy");
  copyButton.type = "button";
  copyButton.title = "Copy full command";
  copyButton.setAttribute("aria-label", "Copy full process command");
  keepFocus(copyButton, "copy-process-command", host, gpuIndex, pid);
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command);
      if (appStatus) appStatus.textContent = `Copied command for process ${pid}.`;
    } catch (_) {
      if (appStatus) appStatus.textContent = "Could not copy process command.";
    }
  });
  detail.append(section("Full command", commandBlock, copyButton));

  const trends = element("div", "process-metrics-grid");
  append(
    trends,
    processMetric("CPU", entry.cpu, formatPercent),
    processMetric("GPU util", entry.gpuUtil, formatPercent, { max: 100 }),
    processMetric("GPU memory", entry.gpuMemory, formatBytes),
    processMetric("Host memory", entry.hostMemory, formatBytes),
  );
  detail.append(section(
    "Process trends",
    trends,
    element(
      "span",
      "section-count",
      `${entry.timestamps.length} sample${entry.timestamps.length === 1 ? "" : "s"}`,
    ),
  ));
  return detail;
}

function renderProcessTable(rows, scrollKey) {
  const shell = tableShell(PROCESS_TABLE_COLUMNS, scrollKey, {
    sortScope: "processes",
    label: "Processes",
  });
  const sortedRows = sortedTableRows(
    "processes",
    rows,
    PROCESS_TABLE_COLUMNS,
    processTieBreaker,
  );
  for (const { node, process } of sortedRows) {
    const row = document.createElement("tr");
    const hostButton = element("button", "link-button", node.hostname);
    hostButton.type = "button";
    keepFocus(hostButton, "process-host", node.hostname, process.gpu_index, process.pid);
    hostButton.addEventListener("click", () => navigateNode(node.hostname, process.gpu_index));
    cell(row, hostButton, "left");
    cell(row, process.gpu_index);
    const pidLink = element("a", "link-button", process.pid);
    pidLink.href = processHref(node.hostname, process.gpu_index, process.pid);
    pidLink.setAttribute(
      "aria-label",
      `Open process ${process.pid} on ${node.hostname} GPU ${process.gpu_index}`,
    );
    keepFocus(pidLink, "process-pid", node.hostname, process.gpu_index, process.pid);
    pidLink.addEventListener("click", rememberProcessReturnRoute);
    cell(row, pidLink);
    cell(row, process.process_type || "-", "left");
    cell(row, process.user || "-", "left");
    cell(row, formatBytes(process.gpu_memory_bytes));
    cell(row, formatPercent(process.gpu_util_percent));
    cell(row, formatPercent(process.cpu_percent));
    cell(row, formatBytes(process.host_memory_bytes));
    cell(row, formatDuration(process.runtime_seconds));
    const command = process.command || process.name || "-";
    const commandLink = element("a", "link-button", command);
    commandLink.href = processHref(node.hostname, process.gpu_index, process.pid);
    commandLink.setAttribute(
      "aria-label",
      `Open process ${process.pid}: ${command}`,
    );
    commandLink.title = command;
    keepFocus(
      commandLink,
      "process-command",
      node.hostname,
      process.gpu_index,
      process.pid,
    );
    commandLink.addEventListener("click", rememberProcessReturnRoute);
    const commandCell = cell(row, commandLink, "left command-cell");
    commandCell.title = command;
    shell.tbody.append(row);
  }
  if (!rows.length) {
    const row = document.createElement("tr");
    const empty = cell(row, "No matching GPU processes", "table-empty");
    empty.colSpan = 11;
    shell.tbody.append(row);
  }
  return shell.wrap;
}

function renderProcesses() {
  const query = state.searches.processes.trim().toLowerCase();
  const allRows = allProcessRows();
  const rows = allRows.filter(({ node, process }) => {
    return !query || [
      node.hostname,
      process.gpu_index,
      process.pid,
      process.process_type,
      process.user,
      process.command,
      process.name,
    ].some((value) => String(value || "").toLowerCase().includes(query));
  });
  const fragment = document.createDocumentFragment();
  append(
    fragment,
    pageHead("Processes", `${new Set(allRows.map(({ node, process }) => `${node.hostname}:${process.pid}`)).size} unique PIDs`),
    searchToolbar("processes", rows.length, "Search node, GPU, PID, user, or command"),
    renderProcessTable(rows, "process-table"),
  );
  return fragment;
}

function renderGpuTable(node, selectedGpu) {
  const shell = tableShell([
    { label: "GPU", left: true },
    { label: "Model", left: true },
    { label: "Temp" },
    { label: "Power" },
    { label: "Util" },
    { label: "HBM" },
    { label: "Mem BW" },
    { label: "GPU clock" },
    { label: "Mode", left: true },
  ], `node-${node.hostname}-gpus`, {
    label: `GPU devices on ${node.hostname}`,
  });
  shell.wrap.classList.add("node-detail-table");
  for (const device of devicesFor(node)) {
    const row = document.createElement("tr");
    row.className = "clickable";
    row.tabIndex = 0;
    keepFocus(row, "node-gpu", node.hostname, device.index);
    if (device.index === selectedGpu) row.classList.add("selected");
    const select = () => {
      state.selectedGpu[node.hostname] = device.index;
      render();
    };
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
    cell(row, `GPU ${device.index}`, "left node-name");
    cell(row, device.name || "MetaX GPU", "left muted");
    const tempClass = finite(device.temperature_c) && device.temperature_c >= 80 ? "critical" : "";
    cell(row, formatTemperature(device.temperature_c), tempClass);
    const power = finite(device.power_limit_w)
      ? `${Math.round(device.power_w || 0)} / ${Math.round(device.power_limit_w)} W`
      : formatPower(device.power_w);
    cell(row, power);
    cell(row, metricBar(device.gpu_util_percent));
    cell(row, metricBar(memoryPercent(device), "mem", formatMemoryPair(device.memory_used_bytes, device.memory_total_bytes)));
    cell(row, formatPercent(device.memory_bandwidth_util_percent));
    cell(row, finite(device.gpu_clock_mhz) ? `${Math.round(device.gpu_clock_mhz)} MHz` : "-");
    cell(row, device.compute_mode || "-", "left");
    shell.tbody.append(row);
  }
  return shell.wrap;
}

function gpuFilter(node, selectedGpu) {
  const scroll = element("div", "filter-scroll");
  scroll.dataset.scrollKey = `node-${node.hostname}-filter`;
  const filter = element("div", "gpu-filter");
  const options = [{ index: null, label: "All" }, ...devicesFor(node).map((device) => ({
    index: device.index,
    label: `GPU ${device.index}`,
  }))];
  for (const option of options) {
    const active = option.index === selectedGpu;
    const button = element("button", active ? "active" : "", option.label);
    button.type = "button";
    keepFocus(button, "gpu-filter", node.hostname, option.index === null ? "all" : option.index);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.addEventListener("click", () => {
      state.selectedGpu[node.hostname] = option.index;
      render();
    });
    filter.append(button);
  }
  scroll.append(filter);
  return scroll;
}

function renderNodeDetail(host) {
  const nodes = clusterStats().nodes;
  const node = nodes.find((candidate) => candidate.hostname === host);
  const fragment = document.createDocumentFragment();
  if (!node) {
    append(fragment, pageHead("Node not found", host, null, true), element("div", "empty-state", "The node is not in the current inventory"));
    return fragment;
  }
  const stats = nodeStats(node);
  const first = stats.devices[0] || {};
  const version = [first.name, first.driver_version ? `driver ${first.driver_version}` : null, first.maca_version ? `MACA ${first.maca_version}` : null]
    .filter(Boolean).join(" | ");
  const selectedGpu = Object.hasOwn(state.selectedGpu, host) ? state.selectedGpu[host] : null;
  append(
    fragment,
    pageHead(host, version || (node.reachable ? "MetaX GPU node" : "Unavailable"), null, true),
  );
  if (!node.reachable) {
    fragment.append(element("div", "error-band", node.error || "Connection failed"));
  }
  fragment.append(kpiStrip([
    { label: "State", value: node.reachable ? "Online" : "Down", tone: node.reachable ? "good" : "critical" },
    { label: "GPUs", value: stats.gpuCount },
    { label: "Active", value: `${stats.active} / ${stats.gpuCount}` },
    { label: "Avg util", value: formatPercent(stats.util) },
    { label: "HBM", value: formatMemoryPair(stats.used, stats.total) },
    { label: "Peak temp", value: formatTemperature(stats.maxTemp), tone: finite(stats.maxTemp) && stats.maxTemp >= 80 ? "critical" : "" },
    { label: "Power", value: formatPower(stats.power) },
    { label: "Host CPU", value: formatPercent(stats.host.cpu_percent) },
    { label: "Host RAM", value: formatMemoryPair(stats.host.memory_used_bytes, stats.host.memory_total_bytes), tone: finite(stats.host.memory_percent) && stats.host.memory_percent >= 95 ? "critical" : "" },
    { label: "Load", value: formatLoad(stats.host.load_average_1m) },
    { label: "Uptime", value: formatDuration(stats.host.uptime_seconds) },
  ]));
  fragment.append(section("GPU devices", renderGpuTable(node, selectedGpu), element("span", "section-count", `${stats.gpuCount} devices`)));
  const renderHistory = historyForRender();
  const nodeHistory = renderHistory.nodes.get(host);
  if (nodeHistory && nodeHistory.util.filter(finite).length >= 2) {
    const strip = element("div", "trend-strip");
    append(
      strip,
      trendCard("GPU util", nodeHistory.util, formatPercent, { max: 100 }),
      trendCard("HBM used", nodeHistory.memory, formatPercent, { max: 100 }),
    );
    fragment.append(
      section(
        "Trends",
        strip,
        element("span", "section-count", `${renderHistory.timestamps.length} samples`),
      ),
    );
  }
  const processRows = stats.processes
    .filter((process) => selectedGpu === null || process.gpu_index === selectedGpu)
    .map((process) => ({ node, process }));
  const processBlock = element("div");
  append(processBlock, gpuFilter(node, selectedGpu), renderProcessTable(processRows, `node-${host}-processes`));
  fragment.append(section("GPU processes", processBlock, element("span", "section-count", `${processRows.length} rows`)));
  return fragment;
}

function updateShell() {
  const stats = clusterStats();
  clusterSummary.textContent = stats.nodeCount
    ? `${stats.onlineCount}/${stats.nodeCount} nodes | ${stats.gpuCount} GPUs | ${stats.processCount} procs`
    : "waiting for data";
  connectionState.classList.toggle("live", state.connected);
  connectionState.classList.toggle("offline", !state.connected && Boolean(state.cluster));
  connectionLabel.textContent = state.connected ? "Live" : state.cluster ? "Reconnecting" : "Connecting";
  const transportState = state.connected
    ? "SSE live"
    : state.cluster ? "SSE reconnecting" : "SSE connecting";
  refreshState.textContent = state.paused
    ? `${transportState} | Paused${state.bufferedUpdates ? ` | ${state.bufferedUpdates} buffered` : ""}`
    : transportState;
  pauseButton.disabled = !state.cluster;
  clearHistoryButton.disabled = !state.cluster || !historyPersistence.ready
    || historyPersistence.clearing;
  downloadButton.disabled = !state.cluster;
  pauseButton.setAttribute("aria-pressed", state.paused ? "true" : "false");
  pauseButton.title = state.paused
    ? "Resume dashboard updates (P)"
    : "Pause dashboard updates (P)";
  pauseCount.textContent = state.bufferedUpdates
    ? state.bufferedUpdates > 999 ? "999+" : String(state.bufferedUpdates)
    : "";
  pauseCount.hidden = state.bufferedUpdates === 0;
  if (state.cluster && finite(state.cluster.timestamp)) {
    const timestamp = new Date(state.cluster.timestamp * 1000).toLocaleTimeString();
    sampleTime.textContent = state.paused
      ? `Paused at ${timestamp}`
      : `Updated ${timestamp}`;
  } else {
    sampleTime.textContent = "No sample yet";
  }
  const route = currentRoute();
  const activeRoute = route.name === "node"
    ? "nodes"
    : route.name === "process" ? "processes" : route.name;
  for (const button of navButtons) {
    const active = button.dataset.route === activeRoute;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  }
}

function render() {
  const transient = captureTransientState();
  const route = currentRoute();
  const nextRouteKey = routeKey(route);
  const routeChanged = state.renderedRouteKey !== nextRouteKey;
  updateShell();
  if (!state.cluster || !Array.isArray(state.cluster.nodes)) {
    restoreTransientState(transient);
    return;
  }
  let content;
  if (route.name === "nodes") content = renderNodes();
  else if (route.name === "processes") content = renderProcesses();
  else if (route.name === "node") content = renderNodeDetail(route.host);
  else if (route.name === "process") {
    content = renderProcessDetail(route.host, route.gpuIndex, route.pid);
  }
  else content = renderOverview();
  app.replaceChildren(content);
  state.renderedRouteKey = nextRouteKey;
  if (routeChanged) {
    const title = app.querySelector(".page-title");
    if (title instanceof HTMLElement) {
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    }
  } else {
    restoreTransientState(transient);
  }
  syncTableScrollRegions();
}

function setPaused(paused) {
  if (paused === state.paused) return;
  if (paused) {
    if (!state.cluster) {
      if (appStatus) {
        appStatus.textContent = "Updates can be paused after the first sample arrives.";
      }
      return;
    }
    state.pausedHistory = cloneHistory(history);
    state.latestCluster = state.latestCluster || state.cluster;
    state.paused = true;
    state.bufferedUpdates = 0;
    updateShell();
    if (appStatus) {
      appStatus.textContent = `Dashboard updates paused. Showing sample from ${formatTimestamp(state.cluster.timestamp)}.`;
    }
    return;
  }

  const bufferedUpdates = state.bufferedUpdates;
  const statusBeforeRender = appStatus ? appStatus.textContent : "";
  state.paused = false;
  if (state.latestCluster) state.cluster = state.latestCluster;
  state.pausedHistory = null;
  state.bufferedUpdates = 0;
  render();
  if (appStatus) {
    const resumeMessage = bufferedUpdates
      ? `Dashboard updates resumed. Showing the latest sample from ${formatTimestamp(state.cluster.timestamp)}.`
      : "Dashboard updates resumed.";
    const transitionMessage = appStatus.textContent !== statusBeforeRender
      ? appStatus.textContent
      : "";
    appStatus.textContent = transitionMessage
      ? `${resumeMessage} ${transitionMessage}`
      : resumeMessage;
  }
}

function togglePaused() {
  setPaused(!state.paused);
}

for (const button of navButtons) {
  button.addEventListener("click", () => navigate(button.dataset.route));
}

pauseButton.addEventListener("click", togglePaused);

clearHistoryButton.addEventListener("click", () => {
  if (!clearHistoryDialog || clearHistoryButton.disabled) return;
  clearHistoryDialog.returnValue = "";
  clearHistoryButton.setAttribute("aria-expanded", "true");
  clearHistoryDialog.showModal();
});

clearHistoryDialog.addEventListener("close", () => {
  clearHistoryButton.setAttribute("aria-expanded", "false");
  if (clearHistoryDialog.returnValue === "clear") void clearIncidentHistory();
});

const themeToggle = document.getElementById("theme-toggle");

function syncThemeToggle() {
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  themeToggle.textContent = theme === "light" ? "☾" : "☀";
  themeToggle.setAttribute(
    "aria-label",
    theme === "light" ? "Switch to dark theme" : "Switch to light theme",
  );
}

themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("mxtop-theme", next); } catch (_) {}
  syncThemeToggle();
});

syncThemeToggle();

const downloadButton = document.getElementById("download-snapshot");
downloadButton.addEventListener("click", () => {
  if (!state.cluster) return;
  const stamp = finite(state.cluster.timestamp)
    ? new Date(state.cluster.timestamp * 1000).toISOString().replace(/[:.]/g, "-")
    : "snapshot";
  const blob = new Blob(
    [JSON.stringify(state.cluster, null, 2)],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mxtop-cluster-${stamp}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

function _isTypingTarget(target) {
  return target instanceof HTMLElement
    && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
}

document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  if (clearHistoryDialog.open) return;
  if (_isTypingTarget(event.target)) {
    if (event.key === "Escape") event.target.blur();
    return;
  }
  if (!event.repeat && (event.key === "p" || event.key === "Z")) {
    event.preventDefault();
    togglePaused();
  } else if (event.key === "1") navigate("overview");
  else if (event.key === "2") navigate("nodes");
  else if (event.key === "3") navigate("processes");
  else if (event.key === "/") {
    const box = document.querySelector(".search-box");
    if (box) {
      event.preventDefault();
      box.focus();
    }
  }
});

window.addEventListener("hashchange", render);
window.addEventListener("resize", syncTableScrollRegions);

if (!window.location.hash) {
  window.history.replaceState(null, "", "#/overview");
}

fetch("/api/snapshot")
  .then((response) => response.ok ? response.json() : null)
  .then((cluster) => {
    if (cluster && Array.isArray(cluster.nodes)
        && !state.latestCluster && historyPersistence.pending.length === 0) {
      receiveCluster(cluster);
    }
  })
  .catch(() => {});

const stream = new EventSource("/api/stream");
stream.onopen = () => {
  state.connected = true;
  updateShell();
};
stream.onmessage = (event) => {
  try {
    const cluster = JSON.parse(event.data);
    if (cluster && Array.isArray(cluster.nodes)) {
      state.connected = true;
      receiveCluster(cluster);
    }
  } catch (_) {
    refreshState.textContent = "Invalid sample";
  }
};
stream.onerror = () => {
  state.connected = false;
  updateShell();
};

window.addEventListener("offline", () => {
  state.connected = false;
  updateShell();
});
window.addEventListener("online", () => {
  state.connected = stream.readyState === EventSource.OPEN;
  updateShell();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") void persistHistoryNow();
});
window.addEventListener("pagehide", () => { void persistHistoryNow(); });

render();
