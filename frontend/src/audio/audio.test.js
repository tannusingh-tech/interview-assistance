import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChunkedRecorder } from "./ChunkedRecorder.js";
import { TranscriptionSocket } from "./TranscriptionSocket.js";
import { mapMicError, mergeSegments, pickMimeType, toWsUrl } from "./utils.js";

describe("utils", () => {
  it("builds websocket URLs", () => {
    expect(toWsUrl("http://localhost:8000")).toBe("ws://localhost:8000/ws/transcribe");
    expect(toWsUrl("https://api.example.com/base/")).toBe("wss://api.example.com/base/ws/transcribe");
  });

  it("maps microphone errors to actionable messages", () => {
    expect(mapMicError({ name: "NotAllowedError" })).toMatch(/blocked/i);
    expect(mapMicError({ name: "NotFoundError" })).toMatch(/No microphone/i);
    expect(mapMicError({ name: "NotReadableError" })).toMatch(/could not be opened/i);
    expect(mapMicError(new Error("weird"))).toMatch(/Could not start/i);
  });

  it("merges segments without duplicating ids", () => {
    const a = [{ id: "1:1-0", text: "hi" }];
    expect(mergeSegments(a, [{ id: "1:1-0", text: "hi" }])).toBe(a);
    expect(mergeSegments(a, [{ id: "1:1-0", text: "hi" }, { id: "1:2-0", text: "there" }]).map((s) => s.id)).toEqual([
      "1:1-0",
      "1:2-0",
    ]);
  });

  it("picks the first supported mime type, '' as fallback, null when unsupported", () => {
    expect(pickMimeType({ isTypeSupported: (t) => t === "audio/webm" })).toBe("audio/webm");
    expect(pickMimeType({ isTypeSupported: () => false })).toBe("");
    expect(pickMimeType(undefined)).toBeNull();
  });
});

class FakeRecorder {
  static instances = [];
  constructor() {
    this.state = "inactive";
    this.mimeType = "audio/webm";
    this.size = 2000;
    FakeRecorder.instances.push(this);
  }
  start() { this.state = "recording"; }
  stop() {
    if (this.state === "inactive") throw new Error("InvalidState");
    this.state = "inactive";
    queueMicrotask(() => {
      this.ondataavailable?.({ data: new Blob([new Uint8Array(this.size)]) });
      this.onstop?.();
    });
  }
}

describe("ChunkedRecorder", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeRecorder.instances = [];
    globalThis.MediaRecorder = FakeRecorder;
  });
  afterEach(() => {
    vi.useRealTimers();
    delete globalThis.MediaRecorder;
  });

  it("emits a complete chunk and starts a fresh recorder every interval", async () => {
    const chunks = [];
    const r = new ChunkedRecorder({}, { chunkMs: 4000, onChunk: (b) => chunks.push(b), onError: () => {} });
    r.start();
    await vi.advanceTimersByTimeAsync(4000);
    expect(chunks).toHaveLength(1);
    expect(FakeRecorder.instances).toHaveLength(2);
    expect(FakeRecorder.instances[1].state).toBe("recording");
    await vi.advanceTimersByTimeAsync(4000);
    expect(chunks).toHaveLength(2);
  });

  it("stop() resolves only after the final chunk is emitted", async () => {
    const chunks = [];
    const r = new ChunkedRecorder({}, { chunkMs: 4000, onChunk: (b) => chunks.push(b), onError: () => {} });
    r.start();
    await vi.advanceTimersByTimeAsync(1500);
    await r.stop();
    expect(chunks).toHaveLength(1);
    expect(FakeRecorder.instances).toHaveLength(1);
    expect(FakeRecorder.instances[0].state).toBe("inactive");
  });

  it("stop() during a timer-triggered stop still delivers exactly one final chunk", async () => {
    const chunks = [];
    const r = new ChunkedRecorder({}, { chunkMs: 1000, onChunk: (b) => chunks.push(b), onError: () => {} });
    r.start();
    vi.advanceTimersByTime(1000); // timer fired; recorder stop() queued, onstop not yet run
    await r.stop();
    expect(chunks).toHaveLength(1);
    expect(FakeRecorder.instances.every((x) => x.state === "inactive")).toBe(true);
  });

  it("skips chunks smaller than minBytes", async () => {
    const chunks = [];
    const r = new ChunkedRecorder({}, { chunkMs: 1000, minBytes: 5000, onChunk: (b) => chunks.push(b), onError: () => {} });
    r.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(chunks).toHaveLength(0);
  });

  it("abort() discards audio and stops the recorder", async () => {
    const chunks = [];
    const r = new ChunkedRecorder({}, { chunkMs: 4000, onChunk: (b) => chunks.push(b), onError: () => {} });
    r.start();
    r.abort();
    await vi.advanceTimersByTimeAsync(10000);
    expect(chunks).toHaveLength(0);
    expect(FakeRecorder.instances[0].state).toBe("inactive");
    expect(FakeRecorder.instances).toHaveLength(1);
  });
});

class FakeWebSocket {
  static OPEN = 1;
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(d) { this.sent.push(d); }
  close() { this.readyState = 3; }
  emit(obj) { this.onmessage?.({ data: JSON.stringify(obj) }); }
}

describe("TranscriptionSocket", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket;
  });
  afterEach(() => { delete globalThis.WebSocket; });

  it("resolves open() on ready and forwards messages", async () => {
    const seen = [];
    const s = new TranscriptionSocket("ws://x", { onMessage: (m) => seen.push(m.type), onClose: () => {} });
    const p = s.open();
    FakeWebSocket.instances[0].emit({ type: "status", state: "loading_model" });
    FakeWebSocket.instances[0].emit({ type: "ready" });
    await expect(p).resolves.toMatchObject({ type: "ready" });
    expect(seen).toEqual(["status", "ready"]);
  });

  it("rejects open() on a connection error and on a fatal server error", async () => {
    const s1 = new TranscriptionSocket("ws://x", { onMessage: () => {}, onClose: () => {} });
    const p1 = s1.open();
    FakeWebSocket.instances[0].onerror();
    await expect(p1).rejects.toThrow(/Could not connect/);

    const s2 = new TranscriptionSocket("ws://x", { onMessage: () => {}, onClose: () => {} });
    const p2 = s2.open();
    FakeWebSocket.instances[1].emit({ type: "error", fatal: true, message: "model failed" });
    await expect(p2).rejects.toThrow("model failed");
  });

  it("sends chunks in order, then stop, and waits for 'stopped'", async () => {
    const s = new TranscriptionSocket("ws://x", { onMessage: () => {}, onClose: () => {} });
    const p = s.open();
    const ws = FakeWebSocket.instances[0];
    ws.emit({ type: "ready" });
    await p;
    s.sendChunk(new Blob([new Uint8Array(500)]));
    s.sendChunk(new Blob([new Uint8Array(10)]));
    const done = s.finish(5000);
    await vi.waitFor(() => expect(ws.sent).toHaveLength(3));
    expect(ws.sent[0].byteLength).toBe(500);
    expect(ws.sent[1].byteLength).toBe(10);
    expect(JSON.parse(ws.sent[2])).toEqual({ type: "stop" });
    ws.emit({ type: "stopped" });
    await expect(done).resolves.toBeUndefined();
  });
});
