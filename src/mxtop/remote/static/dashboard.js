"use strict";

const app = document.getElementById("app");
const clusterSummary = document.getElementById("cluster-summary");
const connectionState = document.getElementById("connection-state");
const connectionLabel = document.getElementById("connection-label");
const sampleTime = document.getElementById("sample-time");
const refreshState = document.getElementById("refresh-state");
const navButtons = [...document.querySelectorAll("[data-route]")];

const state = {
  cluster: null,
  connected: false,
  heatMetric: "util",
  searches: { nodes: "", processes: "" },
  selectedGpu: {},
};

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

function clusterStats() {
  const nodes = state.cluster && Array.isArray(state.cluster.nodes) ? state.cluster.nodes : [];
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

function navigate(route) {
  window.location.hash = `#/${route}`;
}

function navigateNode(host, gpuIndex = null) {
  if (gpuIndex !== null) state.selectedGpu[host] = gpuIndex;
  navigate(`node/${encodeURIComponent(host)}`);
}

function captureTransientState() {
  const active = document.activeElement;
  const focus = active instanceof HTMLInputElement && active.dataset.focusKey
    ? {
      key: active.dataset.focusKey,
      start: active.selectionStart,
      end: active.selectionEnd,
    }
    : null;
  const scroll = {};
  document.querySelectorAll("[data-scroll-key]").forEach((node) => {
    scroll[node.dataset.scrollKey] = node.scrollLeft;
  });
  return { focus, scroll };
}

function restoreTransientState(transient) {
  for (const [key, left] of Object.entries(transient.scroll)) {
    const node = document.querySelector(`[data-scroll-key="${key}"]`);
    if (node) node.scrollLeft = left;
  }
  if (!transient.focus) return;
  const input = document.querySelector(`[data-focus-key="${transient.focus.key}"]`);
  if (!(input instanceof HTMLInputElement)) return;
  input.focus();
  if (transient.focus.start !== null && transient.focus.end !== null) {
    input.setSelectionRange(transient.focus.start, transient.focus.end);
  }
}

function pageHead(title, meta = "", actions = null, back = false) {
  const head = element("div", "page-head");
  const titleWrap = element("div", back ? "title-with-back" : "page-title-wrap");
  if (back) {
    const button = element("button", "back-button");
    button.type = "button";
    button.title = "Back to nodes";
    button.setAttribute("aria-label", "Back to nodes");
    button.addEventListener("click", () => navigate("nodes"));
    titleWrap.append(button);
  }
  const labels = element("div", "page-title-wrap");
  append(labels, element("h1", "page-title", title));
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

function tableShell(headers, scrollKey) {
  const wrap = element("div", "table-wrap");
  wrap.dataset.scrollKey = scrollKey;
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const row = document.createElement("tr");
  for (const header of headers) {
    row.append(element("th", header.left ? "left" : "", header.label));
  }
  thead.append(row);
  const tbody = document.createElement("tbody");
  append(table, thead, tbody);
  wrap.append(table);
  return { wrap, table, tbody };
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
  return view;
}

function makeClickableRow(row, host) {
  row.classList.add("clickable");
  row.tabIndex = 0;
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
  const shell = tableShell([
    { label: "Node", left: true },
    { label: "State", left: true },
    { label: "GPUs" },
    { label: "Active" },
    { label: "Util" },
    { label: "HBM" },
    { label: "Peak temp" },
    { label: "Power" },
    { label: "CPU" },
    { label: "RAM" },
    { label: "Load" },
    { label: "Procs" },
    { label: "SSH" },
  ], scrollKey);
  for (const node of nodes) {
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
    target.addEventListener("click", () => navigateNode(item.node.hostname, item.device ? item.device.index : null));
    if (item.down) {
      append(row, target, element("span", "hotspot-metric critical", "DOWN"));
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

function renderOverview() {
  const stats = clusterStats();
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
    section("GPU matrix", renderHeatmap(stats), controls),
  );
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
    })
    .sort((left, right) => Number(left.reachable) - Number(right.reachable)
      || left.hostname.localeCompare(right.hostname));
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

function renderProcessTable(rows, scrollKey) {
  const shell = tableShell([
    { label: "Node", left: true },
    { label: "GPU" },
    { label: "PID" },
    { label: "Type", left: true },
    { label: "User", left: true },
    { label: "GPU memory" },
    { label: "GPU util" },
    { label: "CPU" },
    { label: "Host memory" },
    { label: "Runtime" },
    { label: "Command", left: true },
  ], scrollKey);
  for (const { node, process } of rows) {
    const row = document.createElement("tr");
    const hostButton = element("button", "link-button", node.hostname);
    hostButton.type = "button";
    hostButton.addEventListener("click", () => navigateNode(node.hostname, process.gpu_index));
    cell(row, hostButton, "left");
    cell(row, process.gpu_index);
    cell(row, process.pid);
    cell(row, process.process_type || "-", "left");
    cell(row, process.user || "-", "left");
    cell(row, formatBytes(process.gpu_memory_bytes));
    cell(row, formatPercent(process.gpu_util_percent));
    cell(row, formatPercent(process.cpu_percent));
    cell(row, formatBytes(process.host_memory_bytes));
    cell(row, formatDuration(process.runtime_seconds));
    const command = process.command || process.name || "-";
    const commandCell = cell(row, command, "left command-cell");
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
  }).sort((left, right) => {
    return (right.process.gpu_memory_bytes || 0) - (left.process.gpu_memory_bytes || 0)
      || left.node.hostname.localeCompare(right.node.hostname)
      || left.process.gpu_index - right.process.gpu_index;
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
  ], `node-${node.hostname}-gpus`);
  shell.wrap.classList.add("node-detail-table");
  for (const device of devicesFor(node)) {
    const row = document.createElement("tr");
    row.className = "clickable";
    row.tabIndex = 0;
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
  refreshState.textContent = state.connected ? "SSE live" : "SSE reconnecting";
  sampleTime.textContent = state.cluster && finite(state.cluster.timestamp)
    ? `Updated ${new Date(state.cluster.timestamp * 1000).toLocaleTimeString()}`
    : "No sample yet";
  const route = currentRoute();
  const activeRoute = route.name === "node" ? "nodes" : route.name;
  for (const button of navButtons) {
    const active = button.dataset.route === activeRoute;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  }
}

function render() {
  const transient = captureTransientState();
  updateShell();
  if (!state.cluster || !Array.isArray(state.cluster.nodes)) {
    restoreTransientState(transient);
    return;
  }
  const route = currentRoute();
  let content;
  if (route.name === "nodes") content = renderNodes();
  else if (route.name === "processes") content = renderProcesses();
  else if (route.name === "node") content = renderNodeDetail(route.host);
  else content = renderOverview();
  app.replaceChildren(content);
  restoreTransientState(transient);
}

for (const button of navButtons) {
  button.addEventListener("click", () => navigate(button.dataset.route));
}

window.addEventListener("hashchange", render);

if (!window.location.hash) {
  window.history.replaceState(null, "", "#/overview");
}

fetch("/api/snapshot")
  .then((response) => response.ok ? response.json() : null)
  .then((cluster) => {
    if (cluster && Array.isArray(cluster.nodes)) {
      state.cluster = cluster;
      render();
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
      state.cluster = cluster;
      state.connected = true;
      render();
    }
  } catch (_) {
    refreshState.textContent = "Invalid sample";
  }
};
stream.onerror = () => {
  state.connected = false;
  updateShell();
};

render();
