"""Flask app: serves the dashboard and a small REST API."""
from __future__ import annotations

import os

from flask import Flask, g, jsonify, request, send_from_directory

import database
from process_uploads import MAX_UPLOAD_BYTES, upload_files


def create_app(db_path: str | os.PathLike | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES * 5  # per request (several files)
    app.config["DB_PATH"] = str(db_path or database.get_db_path())

    # Create tables and seed reference data once, at startup.
    conn = database.connect(app.config["DB_PATH"])
    try:
        database.init_db(conn)
    finally:
        conn.close()

    def get_conn():
        if "conn" not in g:
            g.conn = database.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"error": "Upload is too large (20 MB per file)."}), 413

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "Not found"}), 404

    # ---- pages -----------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    # ---- API -------------------------------------------------------------
    @app.get("/api/health")
    def health():
        get_conn().execute("SELECT 1")
        return jsonify({"status": "ok", "service": "nach-bounce-tracker"})

    @app.post("/api/uploads")
    def upload_endpoint():
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            return jsonify({"error": "No files uploaded. Use multipart field name 'files'."}), 400
        results = upload_files(get_conn(), [(f.filename, f.read()) for f in files])
        return jsonify({"results": results}), 200

    @app.get("/api/uploads")
    def upload_history():
        rows = get_conn().execute("""
            SELECT id, file_name, instrument_type, sponsor_bank, row_count, status,
                   error_message, uploaded_at
            FROM import_batches ORDER BY id DESC LIMIT 50
        """).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/api/summary")
    def summary():
        conn = get_conn()
        one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        raw = one("SELECT COUNT(*) FROM raw_returns")
        events = one("SELECT COUNT(*) FROM bounce_events")
        rows = lambda sql: [dict(x) for x in conn.execute(sql).fetchall()]  # noqa: E731
        return jsonify({
            "raw_returns": raw,
            "unique_events": events,
            "duplicates_suppressed": raw - events,
            "charges": one("SELECT COUNT(*) FROM charges"),
            "charge_total": one("SELECT COALESCE(SUM(charge_amount),0) FROM charges"),
            "needs_review": one("SELECT COUNT(*) FROM bounce_events WHERE canonical_code='UNMAPPED'"),
            "by_instrument": rows("""SELECT instrument_type AS name, COUNT(*) AS value
                                     FROM bounce_events GROUP BY instrument_type ORDER BY value DESC"""),
            "by_reason": rows("""SELECT COALESCE(m.display_name, be.canonical_code) AS name, COUNT(*) AS value
                                 FROM bounce_events be
                                 LEFT JOIN master_reason_codes m ON m.canonical_code=be.canonical_code
                                 GROUP BY be.canonical_code ORDER BY value DESC"""),
            "by_bank": rows("""SELECT sponsor_bank AS name, COUNT(*) AS value
                               FROM bounce_events GROUP BY sponsor_bank ORDER BY value DESC"""),
        })

    @app.get("/api/events")
    def events():
        q = request.args.get("q", "").strip()
        instrument = request.args.get("instrument", "").strip().upper()
        bank = request.args.get("bank", "").strip().upper()
        reason = request.args.get("reason", "").strip()
        limit = min(max(request.args.get("limit", default=500, type=int), 1), 2000)
        offset = max(request.args.get("offset", default=0, type=int), 0)

        where, params = [], []
        if q:
            where.append("(be.customer_name LIKE ? OR be.primary_ref LIKE ? OR be.account_no LIKE ?)")
            params += [f"%{q}%"] * 3
        if instrument in ("NACH", "CHEQUE"):
            where.append("be.instrument_type=?")
            params.append(instrument)
        if bank:
            where.append("be.sponsor_bank=?")
            params.append(bank)
        if reason:
            where.append("be.canonical_code=?")
            params.append(reason)

        sql = """SELECT be.id, be.instrument_type, be.primary_ref, be.secondary_ref,
                        be.customer_name, be.account_no, be.amount, be.canonical_code,
                        COALESCE(m.display_name, be.canonical_code) AS reason,
                        be.sponsor_bank, be.attempt_count,
                        CASE WHEN c.id IS NULL THEN 0 ELSE 1 END AS charged,
                        COALESCE(c.charge_amount, 0) AS charge_amount
                 FROM bounce_events be
                 LEFT JOIN master_reason_codes m ON m.canonical_code=be.canonical_code
                 LEFT JOIN charges c ON c.bounce_event_id=be.id"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY be.id DESC LIMIT ? OFFSET ?"
        rows = get_conn().execute(sql, params + [limit, offset]).fetchall()
        return jsonify([dict(x) for x in rows])

    @app.get("/api/events/<int:event_id>")
    def event_detail(event_id: int):
        conn = get_conn()
        event = conn.execute("""
            SELECT be.*, COALESCE(m.display_name, be.canonical_code) AS reason, m.category,
                   m.is_chargeable, c.charge_amount, c.notice_text
            FROM bounce_events be
            LEFT JOIN master_reason_codes m ON m.canonical_code=be.canonical_code
            LEFT JOIN charges c ON c.bounce_event_id=be.id
            WHERE be.id=?""", (event_id,)).fetchone()
        if not event:
            return jsonify({"error": "Event not found"}), 404
        attempts = conn.execute("""
            SELECT pa.presentation_seq, pa.return_date, rr.raw_code, rr.raw_description,
                   rr.sponsor_bank
            FROM presentation_attempts pa JOIN raw_returns rr ON rr.id=pa.raw_return_id
            WHERE pa.bounce_event_id=? ORDER BY pa.presentation_seq, pa.id""",
            (event_id,)).fetchall()
        result = dict(event)
        result["attempts"] = [dict(x) for x in attempts]
        return jsonify(result)

    @app.get("/api/charges")
    def charges():
        rows = get_conn().execute("""
            SELECT id, bounce_event_id, instrument_type, primary_ref, customer_name,
                   account_no, reason, charge_amount, notice_text
            FROM charges ORDER BY id DESC LIMIT 500""").fetchall()
        return jsonify([dict(x) for x in rows])

    @app.get("/api/meta")
    def meta():
        conn = get_conn()
        banks = [r[0] for r in conn.execute(
            "SELECT DISTINCT sponsor_bank FROM bounce_events ORDER BY sponsor_bank")]
        reasons = [dict(r) for r in conn.execute(
            "SELECT canonical_code, display_name FROM master_reason_codes ORDER BY display_name")]
        return jsonify({"banks": banks, "reasons": reasons})

    return app


if __name__ == "__main__":
    create_app().run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
