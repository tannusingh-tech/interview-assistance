const STATUS_TEXT = {
  requesting_mic: "Waiting for microphone permission…",
  connecting: "Connecting to the backend…",
  loading_model:
    "Loading the speech model. The first run downloads it and can take a few minutes.",
  listening: "Listening. Text appears a few seconds after you speak.",
  stopping: "Finishing the last bit of audio…",
};

/**
 * segments: [{ id, text }]  confirmed text, appended once per id.
 * interim:  provisional status shown in italics (e.g. "Transcribing…"); never stored as text.
 */
export default function TranscriptPanel({ segments, interim, status }) {
  const caption = STATUS_TEXT[status];
  const showEmpty = segments.length === 0 && !interim && !caption;
  return (
    <section className="panel transcript" aria-labelledby="transcript-title">
      <h2 id="transcript-title">
        Transcript
        {status === "listening" && <span className="live" aria-label="Listening" />}
      </h2>
      {caption && <p className="hint">{caption}</p>}
      <div className="transcript__body" aria-live="polite">
        {showEmpty && (
          <p className="empty">Press Start session to begin. Spoken words will appear here.</p>
        )}
        {segments.map((s) => (
          <p key={s.id} className="seg seg--final">{s.text}</p>
        ))}
        {interim && <p className="seg seg--interim">{interim}</p>}
      </div>
    </section>
  );
}
