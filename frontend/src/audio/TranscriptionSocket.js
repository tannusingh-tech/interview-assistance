/** WebSocket client for the backend /ws/transcribe endpoint. */
export class TranscriptionSocket {
  constructor(url, { onMessage, onClose }) {
    this.url = url;
    this.onMessage = onMessage;
    this.onClose = onClose;
    this.ws = null;
    this.sendChain = Promise.resolve();
    this.stoppedWaiters = [];
  }

  /** Connect and resolve with the server's "ready" message (after the model has loaded). */
  open(timeoutMs = 600000) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      };
      const timer = setTimeout(() => {
        fail(new Error("Timed out waiting for the speech model to load."));
        this.close();
      }, timeoutMs);

      let ws;
      try {
        ws = new WebSocket(this.url);
      } catch {
        fail(new Error("Invalid backend URL. Check VITE_API_BASE_URL."));
        return;
      }
      ws.binaryType = "arraybuffer";
      this.ws = ws;

      ws.onerror = () =>
        fail(new Error("Could not connect to the backend. Make sure it is running, then try again."));
      ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "stopped") this.stoppedWaiters.splice(0).forEach((r) => r());
        if (!settled && msg.type === "ready") {
          settled = true;
          clearTimeout(timer);
          resolve(msg);
        } else if (!settled && msg.type === "error" && msg.fatal) {
          fail(new Error(msg.message));
        }
        this.onMessage(msg);
      };
      ws.onclose = (ev) => {
        fail(new Error("The backend closed the connection before the speech model was ready."));
        this.stoppedWaiters.splice(0).forEach((r) => r());
        this.onClose(ev);
      };
    });
  }

  /** Queue a complete audio file. Sends keep their order even though Blob reads are async. */
  sendChunk(blob) {
    this.sendChain = this.sendChain.then(async () => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const buf = await blob.arrayBuffer();
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(buf);
    });
    return this.sendChain;
  }

  /** Send a small JSON control message (e.g. interview context). Returns false if not open. */
  sendJson(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  /** Ask the server to transcribe pending audio, then wait for "stopped" (or timeout/close). */
  async finish(timeoutMs = 60000) {
    await this.sendChain.catch(() => {});
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const stopped = new Promise((resolve) => this.stoppedWaiters.push(resolve));
    this.ws.send(JSON.stringify({ type: "stop" }));
    await Promise.race([stopped, new Promise((r) => setTimeout(r, timeoutMs))]);
  }

  close() {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onmessage = null;
      ws.onerror = null;
      try {
        ws.close(1000);
      } catch {
        /* already closed */
      }
    }
  }
}
