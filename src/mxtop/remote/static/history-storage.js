"use strict";

(function initializeHistoryStorage(global) {
  const DB_NAME = "mxtop-dashboard-history";
  const DB_VERSION = 1;
  const STORE_NAME = "records";
  const RECORD_VERSION = 1;
  const PAYLOAD_VERSION = 1;
  const CRYPTO_VERSION = 1;
  const SESSION_VERSION = 1;
  const SESSION_STORAGE_KEY = "mxtop-history-session-v1";
  const SCOPE_PREFIX = "hosts-v1:";
  const TTL_MS = 60 * 60 * 1000;
  const MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024;
  const MAX_CURRENT_SESSION_RECORDS = 3;
  const MAX_TOTAL_RECORDS = 8;
  const AES_KEY_BYTES = 32;
  const IV_BYTES = 12;
  const FUTURE_CLOCK_TOLERANCE_MS = 5 * 60 * 1000;
  const MAP_TAG = "__mxtopHistoryMapV1";

  const STATUS = Object.freeze({
    IDLE: "idle",
    READY: "ready",
    LOADED: "loaded",
    SAVED: "saved",
    EMPTY: "empty",
    EXPIRED: "expired",
    STALE: "stale",
    CLEARED: "cleared",
    KEY_ROTATED: "key-rotated",
    INVALID_CLUSTER: "invalid-cluster",
    INVALID: "invalid",
    TOO_LARGE: "too-large",
    UNAVAILABLE: "unavailable",
  });

  const constants = Object.freeze({
    DB_NAME,
    DB_VERSION,
    STORE_NAME,
    RECORD_VERSION,
    PAYLOAD_VERSION,
    CRYPTO_VERSION,
    SESSION_STORAGE_KEY,
    TTL_MS,
    MAX_PLAINTEXT_BYTES,
    MAX_CURRENT_SESSION_RECORDS,
    MAX_TOTAL_RECORDS,
  });

  let lastStatus = Object.freeze({ ok: true, status: STATUS.IDLE });
  let databasePromise = null;
  let sessionPromise = null;
  let operationChain = Promise.resolve();

  // Public methods always resolve a status object; storage failures never escape.
  function result(ok, status, details = {}) {
    lastStatus = Object.freeze({ ok, status, ...details });
    return lastStatus;
  }

  function availablePrimitives() {
    try {
      return Boolean(
        global.crypto
        && global.crypto.subtle
        && typeof global.crypto.getRandomValues === "function"
        && global.indexedDB
        && global.sessionStorage
        && global.TextEncoder
        && global.TextDecoder
        && global.location,
      );
    } catch (_) {
      return false;
    }
  }

  function isPlainObject(value) {
    if (!value || typeof value !== "object") return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function bytesToHex(bytes) {
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  function bytesToBase64Url(bytes) {
    let binary = "";
    for (const value of bytes) binary += String.fromCharCode(value);
    return global.btoa(binary)
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replace(/=+$/u, "");
  }

  function base64UrlToBytes(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/u.test(value)) return null;
    const padded = value.replaceAll("-", "+").replaceAll("_", "/")
      .padEnd(Math.ceil(value.length / 4) * 4, "=");
    try {
      const binary = global.atob(padded);
      return Uint8Array.from(binary, (character) => character.charCodeAt(0));
    } catch (_) {
      return null;
    }
  }

  function asBytes(value) {
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    return null;
  }

  function validStoredRecordHeader(record, now = Date.now()) {
    const iv = record ? asBytes(record.iv) : null;
    const ciphertext = record ? asBytes(record.ciphertext) : null;
    return basicRecordShape(record)
      && /^[0-9a-f]{32}$/u.test(record.sessionId)
      && typeof record.scope === "string"
      && new RegExp(`^${SCOPE_PREFIX}[0-9a-f]{64}$`, "u").test(record.scope)
      && record.id === `${record.sessionId}:${record.scope}`
      && record.recordVersion === RECORD_VERSION
      && record.payloadVersion === PAYLOAD_VERSION
      && record.cryptoVersion === CRYPTO_VERSION
      && record.savedAtMs >= 0
      && record.savedAtMs <= now + FUTURE_CLOCK_TOLERANCE_MS
      && record.expiresAtMs === record.savedAtMs + TTL_MS
      && finiteNumber(record.lastTimestamp)
      && iv
      && iv.byteLength === IV_BYTES
      && ciphertext
      && ciphertext.byteLength > 16
      && ciphertext.byteLength <= MAX_PLAINTEXT_BYTES + 16;
  }

  async function importAesKey(rawKey) {
    return global.crypto.subtle.importKey(
      "raw",
      rawKey,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  async function createSessionMaterial() {
    if (!availablePrimitives()) throw new Error("history storage unavailable");
    let stored = null;
    try {
      stored = global.sessionStorage.getItem(SESSION_STORAGE_KEY);
    } catch (_) {
      throw new Error("session storage unavailable");
    }
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        const rawKey = base64UrlToBytes(parsed.key);
        if (parsed.version === SESSION_VERSION
            && typeof parsed.sessionId === "string"
            && /^[0-9a-f]{32}$/u.test(parsed.sessionId)
            && rawKey
            && rawKey.byteLength === AES_KEY_BYTES) {
          const key = await importAesKey(rawKey);
          rawKey.fill(0);
          return { sessionId: parsed.sessionId, key };
        }
      } catch (_) {
        // Replace malformed session material below.
      }
    }

    const idBytes = global.crypto.getRandomValues(new Uint8Array(16));
    const rawKey = global.crypto.getRandomValues(new Uint8Array(AES_KEY_BYTES));
    const sessionId = bytesToHex(idBytes);
    const encodedKey = bytesToBase64Url(rawKey);
    try {
      global.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({
        version: SESSION_VERSION,
        sessionId,
        key: encodedKey,
      }));
    } catch (_) {
      rawKey.fill(0);
      throw new Error("session storage unavailable");
    }
    const key = await importAesKey(rawKey);
    rawKey.fill(0);
    return { sessionId, key };
  }

  function sessionMaterial() {
    if (sessionPromise) return sessionPromise;
    const creating = createSessionMaterial();
    sessionPromise = creating;
    void creating.catch(() => {
      if (sessionPromise === creating) sessionPromise = null;
    });
    return creating;
  }

  function resetSessionMaterial() {
    let rotated = true;
    try {
      global.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    } catch (_) {
      rotated = false;
    }
    sessionPromise = null;
    return rotated;
  }

  function openDatabase() {
    if (databasePromise) return databasePromise;
    const opening = new Promise((resolve, reject) => {
      if (!availablePrimitives()) {
        reject(new Error("history storage unavailable"));
        return;
      }
      let settled = false;
      let request;
      try {
        request = global.indexedDB.open(DB_NAME, DB_VERSION);
      } catch (error) {
        reject(error);
        return;
      }
      request.onupgradeneeded = () => {
        const database = request.result;
        const store = database.objectStoreNames.contains(STORE_NAME)
          ? request.transaction.objectStore(STORE_NAME)
          : database.createObjectStore(STORE_NAME, { keyPath: "id" });
        if (!store.indexNames.contains("expiresAtMs")) {
          store.createIndex("expiresAtMs", "expiresAtMs", { unique: false });
        }
        if (!store.indexNames.contains("savedAtMs")) {
          store.createIndex("savedAtMs", "savedAtMs", { unique: false });
        }
        if (!store.indexNames.contains("sessionId")) {
          store.createIndex("sessionId", "sessionId", { unique: false });
        }
      };
      request.onsuccess = () => {
        const database = request.result;
        if (settled) {
          database.close();
          return;
        }
        settled = true;
        database.onversionchange = () => {
          database.close();
          if (databasePromise === opening) databasePromise = null;
        };
        resolve(database);
      };
      request.onerror = () => {
        if (settled) return;
        settled = true;
        reject(request.error || new Error("could not open history storage"));
      };
      request.onblocked = () => {
        if (settled) return;
        settled = true;
        reject(new Error("history storage upgrade blocked"));
      };
    });
    databasePromise = opening;
    void opening.catch(() => {
      if (databasePromise === opening) databasePromise = null;
    });
    return opening;
  }

  function transactionResult(transaction, request) {
    return new Promise((resolve, reject) => {
      let value;
      request.onsuccess = () => { value = request.result; };
      request.onerror = () => {
        try { transaction.abort(); } catch (_) {}
      };
      transaction.oncomplete = () => resolve(value);
      transaction.onerror = () => reject(
        transaction.error || request.error || new Error("history transaction failed"),
      );
      transaction.onabort = () => reject(
        transaction.error || request.error || new Error("history transaction aborted"),
      );
    });
  }

  function readRecord(database, id) {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(id);
    return transactionResult(transaction, request);
  }

  function deleteRecord(database, id) {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const request = transaction.objectStore(STORE_NAME).delete(id);
    return transactionResult(transaction, request);
  }

  function basicRecordShape(record) {
    return isPlainObject(record)
      && typeof record.id === "string"
      && typeof record.sessionId === "string"
      && finiteNumber(record.savedAtMs)
      && finiteNumber(record.expiresAtMs);
  }

  function cleanupRecords(database, currentSessionId, protectedId = null) {
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, "readwrite");
      const store = transaction.objectStore(STORE_NAME);
      const request = store.getAll();
      request.onsuccess = () => {
        const now = Date.now();
        const records = request.result;
        const deleted = new Set();
        for (const record of records) {
          if (!validStoredRecordHeader(record, now) || record.expiresAtMs <= now) {
            deleted.add(record.id);
          }
        }
        const newestFirst = (left, right) => {
          if (left.id === right.id) return 0;
          if (left.id === protectedId) return -1;
          if (right.id === protectedId) return 1;
          return right.savedAtMs - left.savedAtMs;
        };
        const live = records.filter((record) => !deleted.has(record.id));
        const current = live
          .filter((record) => record.sessionId === currentSessionId)
          .sort(newestFirst);
        for (const record of current.slice(MAX_CURRENT_SESSION_RECORDS)) {
          deleted.add(record.id);
        }
        const retained = live.filter((record) => !deleted.has(record.id)).sort(newestFirst);
        for (const record of retained.slice(MAX_TOTAL_RECORDS)) deleted.add(record.id);
        for (const id of deleted) store.delete(id);
      };
      request.onerror = () => {
        try { transaction.abort(); } catch (_) {}
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(
        transaction.error || request.error || new Error("history cleanup failed"),
      );
      transaction.onabort = () => reject(
        transaction.error || request.error || new Error("history cleanup aborted"),
      );
    });
  }

  function removeSessionRecords(database, sessionId, protectedId = null) {
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, "readwrite");
      const store = transaction.objectStore(STORE_NAME);
      const request = store.getAll();
      request.onsuccess = () => {
        for (const record of request.result) {
          const owned = record.sessionId === sessionId
            || (typeof record.id === "string" && record.id.startsWith(`${sessionId}:`));
          if (owned && record.id !== protectedId) store.delete(record.id);
        }
      };
      request.onerror = () => {
        try { transaction.abort(); } catch (_) {}
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(
        transaction.error || request.error || new Error("history cleanup failed"),
      );
      transaction.onabort = () => reject(
        transaction.error || request.error || new Error("history cleanup aborted"),
      );
    });
  }

  function putIfNewer(database, record) {
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, "readwrite");
      const store = transaction.objectStore(STORE_NAME);
      const request = store.get(record.id);
      let outcome = STATUS.SAVED;
      request.onsuccess = () => {
        const existing = request.result;
        if (validStoredRecordHeader(existing)
            && existing.id === record.id
            && existing.sessionId === record.sessionId
            && existing.scope === record.scope
            && existing.lastTimestamp > record.lastTimestamp) {
          outcome = STATUS.STALE;
          return;
        }
        store.put(record);
      };
      request.onerror = () => {
        try { transaction.abort(); } catch (_) {}
      };
      transaction.oncomplete = () => resolve(outcome);
      transaction.onerror = () => reject(
        transaction.error || request.error || new Error("history save failed"),
      );
      transaction.onabort = () => reject(
        transaction.error || request.error || new Error("history save aborted"),
      );
    });
  }

  async function computeScope(cluster) {
    if (!availablePrimitives() || !cluster || !Array.isArray(cluster.nodes)) {
      throw new Error("invalid cluster");
    }
    const hosts = [];
    for (const node of cluster.nodes) {
      if (!node || typeof node.hostname !== "string") throw new Error("invalid cluster");
      const hostname = node.hostname.normalize("NFC");
      if (!hostname || hostname.length > 1024) throw new Error("invalid cluster");
      hosts.push(hostname);
    }
    const canonical = JSON.stringify([
      "mxtop-history-hosts-v1",
      ...[...new Set(hosts)].sort(),
    ]);
    const digest = await global.crypto.subtle.digest(
      "SHA-256",
      new global.TextEncoder().encode(canonical),
    );
    return `${SCOPE_PREFIX}${bytesToHex(new Uint8Array(digest))}`;
  }

  function additionalData(sessionId, scope, savedAtMs, expiresAtMs, lastTimestamp) {
    return new global.TextEncoder().encode(JSON.stringify([
      "mxtop-dashboard-history",
      RECORD_VERSION,
      PAYLOAD_VERSION,
      CRYPTO_VERSION,
      global.location.origin,
      sessionId,
      scope,
      savedAtMs,
      expiresAtMs,
      lastTimestamp,
    ]));
  }

  function payloadReplacer(_key, value) {
    if (value instanceof Map) return { [MAP_TAG]: [...value.entries()] };
    return value;
  }

  function payloadReviver(_key, value) {
    if (isPlainObject(value)
        && Object.keys(value).length === 1
        && Object.hasOwn(value, MAP_TAG)) {
      const entries = value[MAP_TAG];
      if (!Array.isArray(entries)
          || entries.some((entry) => !Array.isArray(entry) || entry.length !== 2)) {
        throw new Error("invalid persisted map");
      }
      return new Map(entries);
    }
    return value;
  }

  async function encryptedRecord(material, scope, payload, lastTimestamp) {
    const savedAtMs = Date.now();
    const expiresAtMs = savedAtMs + TTL_MS;
    let serialized;
    try {
      serialized = JSON.stringify({
        payloadVersion: PAYLOAD_VERSION,
        savedAtMs,
        expiresAtMs,
        lastTimestamp,
        payload,
      }, payloadReplacer);
    } catch (_) {
      return { status: STATUS.INVALID };
    }
    const plaintext = new global.TextEncoder().encode(serialized);
    if (plaintext.byteLength > MAX_PLAINTEXT_BYTES) {
      return { status: STATUS.TOO_LARGE };
    }
    const iv = global.crypto.getRandomValues(new Uint8Array(IV_BYTES));
    const ciphertext = await global.crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv,
        additionalData: additionalData(
          material.sessionId,
          scope,
          savedAtMs,
          expiresAtMs,
          lastTimestamp,
        ),
        tagLength: 128,
      },
      material.key,
      plaintext,
    );
    return {
      status: STATUS.SAVED,
      record: {
        id: `${material.sessionId}:${scope}`,
        recordVersion: RECORD_VERSION,
        payloadVersion: PAYLOAD_VERSION,
        cryptoVersion: CRYPTO_VERSION,
        sessionId: material.sessionId,
        scope,
        savedAtMs,
        expiresAtMs,
        lastTimestamp,
        iv: iv.buffer.slice(iv.byteOffset, iv.byteOffset + iv.byteLength),
        ciphertext,
      },
    };
  }

  function validRecordHeader(record, material, scope) {
    return validStoredRecordHeader(record)
      && record.id === `${material.sessionId}:${scope}`
      && record.sessionId === material.sessionId
      && record.scope === scope
      && record.id === `${record.sessionId}:${record.scope}`;
  }

  async function decryptRecord(record, material, scope) {
    const plaintext = await global.crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: asBytes(record.iv),
        additionalData: additionalData(
          material.sessionId,
          scope,
          record.savedAtMs,
          record.expiresAtMs,
          record.lastTimestamp,
        ),
        tagLength: 128,
      },
      material.key,
      asBytes(record.ciphertext),
    );
    if (plaintext.byteLength > MAX_PLAINTEXT_BYTES) throw new Error("history too large");
    const decoded = new global.TextDecoder("utf-8", { fatal: true }).decode(plaintext);
    const wrapper = JSON.parse(decoded, payloadReviver);
    if (!isPlainObject(wrapper)
        || wrapper.payloadVersion !== PAYLOAD_VERSION
        || wrapper.savedAtMs !== record.savedAtMs
        || wrapper.expiresAtMs !== record.expiresAtMs
        || !finiteNumber(wrapper.lastTimestamp)
        || wrapper.lastTimestamp !== record.lastTimestamp
        || !Object.hasOwn(wrapper, "payload")) {
      throw new Error("invalid history payload");
    }
    return wrapper.payload;
  }

  async function authenticatedNewerRecord(database, material, scope, lastTimestamp) {
    const id = `${material.sessionId}:${scope}`;
    const existing = await readRecord(database, id);
    if (!existing) return false;
    if (!validRecordHeader(existing, material, scope) || existing.expiresAtMs <= Date.now()) {
      try { await deleteRecord(database, id); } catch (_) {}
      return false;
    }
    if (existing.lastTimestamp <= lastTimestamp) return false;
    try {
      await decryptRecord(existing, material, scope);
      return true;
    } catch (_) {
      try { await deleteRecord(database, id); } catch (_) {}
      return false;
    }
  }

  function quotaError(error) {
    return error && error.name === "QuotaExceededError";
  }

  function enqueue(operation) {
    const pending = operationChain.then(operation, operation);
    operationChain = pending.then(() => undefined, () => undefined);
    return pending;
  }

  async function scopeForCluster(cluster) {
    try {
      const scope = await computeScope(cluster);
      return result(true, STATUS.READY, { scope });
    } catch (_) {
      const status = availablePrimitives() ? STATUS.INVALID_CLUSTER : STATUS.UNAVAILABLE;
      return result(false, status, { scope: null });
    }
  }

  function load(cluster) {
    return enqueue(async () => {
      let scope = null;
      try {
        scope = await computeScope(cluster);
        const material = await sessionMaterial();
        const database = await openDatabase();
        const id = `${material.sessionId}:${scope}`;
        const record = await readRecord(database, id);
        if (!record) {
          try { await cleanupRecords(database, material.sessionId); } catch (_) {}
          return result(true, STATUS.EMPTY, {
            scope, payload: null, lastTimestamp: null, savedAtMs: null,
          });
        }
        if (!validRecordHeader(record, material, scope)) {
          try { await deleteRecord(database, id); } catch (_) {}
          return result(false, STATUS.INVALID, {
            scope, payload: null, lastTimestamp: null, savedAtMs: null,
          });
        }
        if (record.expiresAtMs <= Date.now()) {
          try { await deleteRecord(database, id); } catch (_) {}
          try { await cleanupRecords(database, material.sessionId); } catch (_) {}
          return result(true, STATUS.EXPIRED, {
            scope, payload: null, lastTimestamp: null, savedAtMs: null,
          });
        }
        let payload;
        try {
          payload = await decryptRecord(record, material, scope);
        } catch (_) {
          try { await deleteRecord(database, id); } catch (_) {}
          return result(false, STATUS.INVALID, {
            scope, payload: null, lastTimestamp: null, savedAtMs: null,
          });
        }
        try { await cleanupRecords(database, material.sessionId, id); } catch (_) {}
        return result(true, STATUS.LOADED, {
          scope,
          payload,
          lastTimestamp: record.lastTimestamp,
          savedAtMs: record.savedAtMs,
        });
      } catch (_) {
        return result(false, STATUS.UNAVAILABLE, {
          scope, payload: null, lastTimestamp: null, savedAtMs: null,
        });
      }
    });
  }

  function save(cluster, payload, lastTimestamp) {
    return enqueue(async () => {
      let scope = null;
      if (!payload || typeof payload !== "object" || !finiteNumber(lastTimestamp)) {
        return result(false, STATUS.INVALID, {
          scope, lastTimestamp, savedAtMs: null,
        });
      }
      try {
        scope = await computeScope(cluster);
        const material = await sessionMaterial();
        const database = await openDatabase();
        if (await authenticatedNewerRecord(
          database,
          material,
          scope,
          lastTimestamp,
        )) {
          return result(true, STATUS.STALE, {
            scope, lastTimestamp, savedAtMs: null,
          });
        }
        const encrypted = await encryptedRecord(material, scope, payload, lastTimestamp);
        if (encrypted.status !== STATUS.SAVED) {
          return result(false, encrypted.status, {
            scope, lastTimestamp, savedAtMs: null,
          });
        }
        const record = encrypted.record;
        try { await cleanupRecords(database, material.sessionId, record.id); } catch (_) {}
        let savedStatus;
        try {
          savedStatus = await putIfNewer(database, record);
        } catch (error) {
          if (!quotaError(error)) throw error;
          try {
            await removeSessionRecords(database, material.sessionId, record.id);
          } catch (_) {}
          savedStatus = await putIfNewer(database, record);
        }
        if (savedStatus === STATUS.STALE) {
          return result(true, STATUS.STALE, {
            scope, lastTimestamp, savedAtMs: null,
          });
        }
        try { await cleanupRecords(database, material.sessionId, record.id); } catch (_) {}
        return result(true, STATUS.SAVED, {
          scope, lastTimestamp, savedAtMs: record.savedAtMs,
        });
      } catch (_) {
        return result(false, STATUS.UNAVAILABLE, {
          scope, lastTimestamp, savedAtMs: null,
        });
      }
    });
  }

  function clear(cluster = null) {
    return enqueue(async () => {
      if (cluster !== null && cluster !== undefined) {
        let scope = null;
        try {
          scope = await computeScope(cluster);
          const material = await sessionMaterial();
          const database = await openDatabase();
          await deleteRecord(database, `${material.sessionId}:${scope}`);
          try { await cleanupRecords(database, material.sessionId); } catch (_) {}
          return result(true, STATUS.CLEARED, {
            scope,
            storageCleared: true,
            keyRotated: false,
          });
        } catch (_) {
          return result(false, STATUS.UNAVAILABLE, {
            scope,
            storageCleared: false,
            keyRotated: false,
          });
        }
      }

      let storageCleared = false;
      try {
        const material = await sessionMaterial();
        const database = await openDatabase();
        await removeSessionRecords(database, material.sessionId);
        storageCleared = true;
      } catch (_) {
        // Rotating the key still makes any surviving ciphertext unreadable.
      }
      const keyRotated = resetSessionMaterial();
      if (!keyRotated) {
        return result(false, STATUS.UNAVAILABLE, { storageCleared, keyRotated });
      }
      if (!storageCleared) {
        return result(false, STATUS.KEY_ROTATED, { storageCleared, keyRotated });
      }
      return result(true, STATUS.CLEARED, {
        storageCleared,
        keyRotated,
      });
    });
  }

  const api = {
    STATUS,
    constants,
    get status() { return lastStatus; },
    scopeForCluster,
    load,
    save,
    clear,
  };
  global.mxtopHistoryStorage = Object.freeze(api);
})(window);
