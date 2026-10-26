/**
 * Records the microphone as a series of COMPLETE, self-contained audio files.
 *
 * Why not MediaRecorder.start(timeslice)? With a timeslice, only the first Blob contains the
 * container header; later Blobs are fragments that cannot be decoded on their own. Instead we
 * stop the recorder every `chunkMs` and immediately start a new one on the same stream, so
 * every emitted Blob is a valid file the backend can decode independently.
 *
 * Trade-offs: text appears roughly chunkMs + transcription time after speech, and a few
 * milliseconds of audio can be lost at each restart.
 */
export class ChunkedRecorder {
  constructor(stream, { chunkMs = 2500, mimeType = "", minBytes = 1000, onChunk, onError }) {
    this.stream = stream;
    this.chunkMs = chunkMs;
    this.mimeType = mimeType;
    this.minBytes = minBytes;
    this.onChunk = onChunk;
    this.onError = onError;
    this.active = false;
    this.rec = null;
    this.timer = null;
    this.pendingStops = 0; // recorders told to stop whose final data has not arrived yet
    this.waiters = [];
  }

  start() {
    if (this.active) return;
    this.active = true;
    this._begin();
  }

  /** Stop recording and resolve once the final chunk has been emitted. */
  stop() {
    this.active = false;
    clearTimeout(this.timer);
    if (this.rec && this.rec.state === "recording") this._stopCurrent();
    return new Promise((resolve) => {
      this.waiters.push(resolve);
      this._settle();
    });
  }

  /** Stop immediately and discard any partial audio (used on errors / unmount). */
  abort() {
    this.active = false;
    clearTimeout(this.timer);
    const rec = this.rec;
    if (rec) {
      rec.ondataavailable = null;
      rec.onstop = null;
      rec.onerror = null;
      try {
        if (rec.state !== "inactive") rec.stop();
      } catch {
        /* already stopped */
      }
    }
    this.rec = null;
    this.pendingStops = 0;
    this._settle();
  }

  _stopCurrent() {
    this.pendingStops += 1;
    try {
      this.rec.stop();
    } catch {
      this.pendingStops -= 1;
    }
  }

  _settle() {
    if (!this.active && this.pendingStops === 0) {
      this.waiters.splice(0).forEach((resolve) => resolve());
    }
  }

  _begin() {
    let rec;
    try {
      rec = new MediaRecorder(this.stream, this.mimeType ? { mimeType: this.mimeType } : undefined);
    } catch (err) {
      this.active = false;
      this.onError(err);
      this._settle();
      return;
    }
    const parts = [];
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size) parts.push(e.data);
    };
    rec.onerror = (e) => this.onError((e && e.error) || new Error("Recorder error"));
    rec.onstop = () => {
      this.pendingStops = Math.max(0, this.pendingStops - 1);
      if (this.active) this._begin(); // start the next recorder first to keep the gap tiny
      const blob = new Blob(parts, { type: rec.mimeType || this.mimeType });
      if (blob.size >= this.minBytes) this.onChunk(blob);
      this._settle();
    };
    this.rec = rec;
    rec.start();
    this.timer = setTimeout(() => {
      if (this.active && this.rec === rec && rec.state === "recording") this._stopCurrent();
    }, this.chunkMs);
  }
}
