export default function QuestionPanel({ question }) {
  return (
    <section className="panel" aria-labelledby="question-title">
      <h2 id="question-title">Detected question</h2>
      {question ? (
        <p className="question">{question}</p>
      ) : (
        <p className="empty">No question detected yet.</p>
      )}
    </section>
  );
}
