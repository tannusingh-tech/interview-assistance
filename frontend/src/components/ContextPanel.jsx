export default function ContextPanel({ resume, jobDescription, onResume, onJobDescription }) {
  return (
    <section className="panel context" aria-labelledby="context-title">
      <h2 id="context-title">Your background</h2>
      <p className="hint">Used to tailor answers. Saved in this browser; while a session runs it is sent to your local backend and on to OpenRouter.</p>

      <label htmlFor="resume">Resume</label>
      <textarea
        id="resume"
        value={resume}
        onChange={(e) => onResume(e.target.value)}
        placeholder="Paste your resume text"
        rows={10}
      />

      <label htmlFor="jd">Job description</label>
      <textarea
        id="jd"
        value={jobDescription}
        onChange={(e) => onJobDescription(e.target.value)}
        placeholder="Paste the job description"
        rows={10}
      />
    </section>
  );
}
