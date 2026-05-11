"""
Integrated Employee Experience Assessment — Flask Web App
Stores responses in PostgreSQL. Auto-scores 95 items across 8 domains.
"""
import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from sqlalchemy import create_engine, Column, String, Integer, DateTime, JSON, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from sqlalchemy.dialects.postgresql import UUID, JSONB
from dotenv import load_dotenv

from questions import QUESTIONS, DEMOGRAPHICS, SCORING_RUBRIC, REVERSE_CODED_ITEMS

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

# --- Database setup ---
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/assessment_db"
)
# Handle Heroku-style URLs (some hosts use postgres:// instead of postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
print(f"Connecting to database at {DATABASE_URL}")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))
Base = declarative_base()


class Response(Base):
    """One row per respondent — answers stored as JSONB for flexibility."""
    __tablename__ = "responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Demographics
    name = Column(String(200), nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String(50), nullable=True)
    job_role = Column(String(200), nullable=True)
    years_experience = Column(String(50), nullable=True)

    # Raw answers: {"Q1": 3, "Q2": 5, ...} (1-5 scale, already normalized)
    answers = Column(JSONB, nullable=False)

    # Computed scores per subdomain: {"Perceived Stress": {"raw": 11, "level": "medium"}, ...}
    scores = Column(JSONB, nullable=False)

    # Metadata
    ip_hash = Column(String(64), nullable=True)  # SHA-256 of IP for dedup, not raw IP
    user_agent = Column(Text, nullable=True)


def init_db():
    """Create tables if they don't exist."""
    Base.metadata.create_all(engine)


@app.teardown_appcontext
def remove_session(exc=None):
    SessionLocal.remove()


# --- Scoring logic ---
def compute_scores(answers: dict) -> dict:
    """
    Apply reverse-coding to flagged items, sum scores per subdomain,
    classify into low/medium/high based on the rubric.
    """
    # Normalize: apply reverse-coding to items marked with *
    normalized = {}
    for qid, val in answers.items():
        if val is None:
            continue
        v = int(val)
        if qid in REVERSE_CODED_ITEMS:
            v = 6 - v  # flip 1->5, 2->4, 3->3, 4->2, 5->1
        normalized[qid] = v

    results = {}
    for entry in SCORING_RUBRIC:
        subdomain = entry["subdomain"]
        domain = entry["domain"]
        items = entry["items"]
        thresholds = entry["thresholds"]  # {"low": (lo, hi), "medium": ..., "high": ...}

        raw_scores = [normalized[f"Q{i}"] for i in items if f"Q{i}" in normalized]
        if not raw_scores:
            continue
        total = sum(raw_scores)

        # Classify
        level = "unknown"
        for lvl in ("low", "medium", "high"):
            lo, hi = thresholds[lvl]
            if lo <= total <= hi:
                level = lvl
                break

        results[subdomain] = {
            "domain": domain,
            "raw_score": total,
            "level": level,
            "item_count": len(raw_scores),
        }

    return results


# --- Routes ---
@app.route("/", methods=["GET"])
def survey_page():
    """Render the survey form."""
    return render_template(
        "survey.html",
        questions=QUESTIONS,
        demographics=DEMOGRAPHICS,
    )


@app.route("/submit", methods=["POST"])
def submit():
    """Receive form submission, score it, save to DB."""
    import hashlib

    form = request.form

    # Pull demographics
    name = form.get("name", "").strip() or None
    age_raw = form.get("age", "").strip()
    try:
        age = int(age_raw) if age_raw else None
    except ValueError:
        age = None
    gender = form.get("gender", "").strip() or None
    job_role = form.get("job_role", "").strip() or None
    years_experience = form.get("years_experience", "").strip() or None

    # Pull Q1..Q95 — stored as integer rank (1..5) of the chosen option
    answers = {}
    for i in range(1, 96):
        val = form.get(f"Q{i}")
        if val:
            try:
                answers[f"Q{i}"] = int(val)
            except ValueError:
                pass

    # Basic validation: require at least 80% of items answered
    if len(answers) < 76:
        return render_template(
            "survey.html",
            questions=QUESTIONS,
            demographics=DEMOGRAPHICS,
            error="Please answer all questions before submitting. "
                  f"You answered {len(answers)} of 95.",
            prefill=form,
        ), 400

    # Score
    scores = compute_scores(answers)

    # Hash IP for analytics without storing PII
    raw_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest() if raw_ip else None

    # Save
    session = SessionLocal()
    try:
        resp = Response(
            name=name,
            age=age,
            gender=gender,
            job_role=job_role,
            years_experience=years_experience,
            answers=answers,
            scores=scores,
            ip_hash=ip_hash,
            user_agent=request.headers.get("User-Agent", "")[:500],
        )
        session.add(resp)
        session.commit()
        response_id = str(resp.id)
    except Exception as e:
        session.rollback()
        app.logger.exception("Failed to save response")
        return f"Server error: {e}", 500
    finally:
        session.close()

    return redirect(url_for("thank_you", rid=response_id))


@app.route("/thank-you")
def thank_you():
    return render_template("thank_you.html")


@app.route("/admin/stats")
def admin_stats():
    """Quick stats endpoint. Protect with ADMIN_TOKEN env var."""
    token = request.args.get("token", "")
    if token != os.environ.get("ADMIN_TOKEN", ""):
        abort(403)

    session = SessionLocal()
    try:
        total = session.query(func.count(Response.id)).scalar()
        recent = session.query(Response).order_by(Response.submitted_at.desc()).limit(50).all()

        # Aggregate level distribution per subdomain
        all_subdomains = {}
        all_responses = session.query(Response).all()
        for r in all_responses:
            for sub, data in (r.scores or {}).items():
                if sub not in all_subdomains:
                    all_subdomains[sub] = {"low": 0, "medium": 0, "high": 0,
                                           "domain": data.get("domain", "")}
                lvl = data.get("level", "unknown")
                if lvl in all_subdomains[sub]:
                    all_subdomains[sub][lvl] += 1

        return jsonify({
            "total_responses": total,
            "subdomain_distribution": all_subdomains,
            "recent_submissions": [
                {
                    "id": str(r.id),
                    "submitted_at": r.submitted_at.isoformat(),
                    "gender": r.gender,
                    "age": r.age,
                    "job_role": r.job_role,
                }
                for r in recent
            ],
        })
    finally:
        session.close()


@app.route("/admin/export.csv")
def admin_export():
    """Export all responses as CSV."""
    import csv
    from io import StringIO
    from flask import Response as FlaskResponse

    token = request.args.get("token", "")
    if token != os.environ.get("ADMIN_TOKEN", ""):
        abort(403)

    session = SessionLocal()
    try:
        rows = session.query(Response).order_by(Response.submitted_at).all()

        out = StringIO()
        writer = csv.writer(out)

        # Header: meta + demographics + Q1..Q95 + score subdomains
        header = ["id", "submitted_at", "name", "age", "gender", "job_role", "years_experience"]
        header += [f"Q{i}" for i in range(1, 96)]
        subdomain_keys = sorted({s["subdomain"] for s in SCORING_RUBRIC})
        header += [f"{k}__raw" for k in subdomain_keys]
        header += [f"{k}__level" for k in subdomain_keys]
        writer.writerow(header)

        for r in rows:
            row = [
                str(r.id),
                r.submitted_at.isoformat(),
                r.name or "",
                r.age or "",
                r.gender or "",
                r.job_role or "",
                r.years_experience or "",
            ]
            row += [r.answers.get(f"Q{i}", "") for i in range(1, 96)]
            row += [r.scores.get(k, {}).get("raw_score", "") for k in subdomain_keys]
            row += [r.scores.get(k, {}).get("level", "") for k in subdomain_keys]
            writer.writerow(row)

        return FlaskResponse(
            out.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=responses.csv"},
        )
    finally:
        session.close()


@app.route("/health")
def health():
    """Health check for AWS load balancers."""
    try:
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        print(f"Health check failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 503


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
