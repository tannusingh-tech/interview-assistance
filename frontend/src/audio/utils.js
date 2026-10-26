/** Pure helpers for the audio session. */

/** http(s)://host:port[/base] -> ws(s)://host:port[/base]/ws/transcribe */
export function toWsUrl(httpBase) {
  const u = new URL(httpBase);
  const proto = u.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${u.host}${u.pathname.replace(/\/+$/, "")}/ws/transcribe`;
}

/** Turn a getUserMedia failure into an actionable message. */
export function mapMicError(err) {
  switch (err && err.name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return "Microphone access was blocked. Allow the microphone for this site in your browser's address bar, then press Start session again.";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "No microphone was found. Connect one, then press Start session again.";
    case "NotReadableError":
    case "TrackStartError":
      return "The microphone could not be opened. Close other apps that are using it, then try again.";
    case "OverconstrainedError":
      return "The microphone does not support the requested audio settings.";
    default:
      return "Could not start the microphone. Check your browser and system audio settings.";
  }
}

/** Append only segments whose id has not been seen. Returns the same array if nothing is new. */
export function mergeSegments(existing, incoming) {
  const seen = new Set(existing.map((s) => s.id));
  const fresh = incoming.filter((s) => !seen.has(s.id));
  return fresh.length ? [...existing, ...fresh] : existing;
}

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

/** First recording format this browser supports ("" = browser default), or null if unsupported. */
export function pickMimeType(MR = globalThis.MediaRecorder) {
  if (!MR) return null;
  if (typeof MR.isTypeSupported !== "function") return "";
  return MIME_CANDIDATES.find((t) => MR.isTypeSupported(t)) ?? "";
}
