"""Upload processing for NACH and cheque return files.

Supported source formats:
- HDFC-style NACH CSV
- ICICI-style NACH pipe-delimited TXT/CSV
- AXIS-style NACH XML (free-text reasons)
- SBI-style cheque CSV

The processor is idempotent:
- an exact file that was already processed is reported as a duplicate (SHA-256);
- an exact raw row that appears in two different files is only stored once;
- bounce events, attempts and charges are rebuilt from the raw rows after every
  successful upload, so files can arrive in any order.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Iterable

from defusedxml import ElementTree as ET  # hardened against XML bombs / XXE

BOUNCE_CHARGE_AMOUNT = float(os.environ.get("BOUNCE_CHARGE_AMOUNT", "250.00"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = (".csv", ".txt", ".xml")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row_hash(*values) -> str:
    raw = "\x1f".join("" if v is None else str(v).strip() for v in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_rows(data: bytes, required: Iterable[str], delimiter: str = ","):
    """Parse delimited text into dicts keyed by lower-cased header names.

    Raises ValueError with a readable message if required columns are missing.
    """
    reader = csv.DictReader(io.StringIO(decode_text(data)), delimiter=delimiter)
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in required if c.lower() not in reader.fieldnames]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))
    for line_no, row in enumerate(reader, start=2):  # header is line 1
        cleaned = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(cleaned.values()):
            continue  # skip blank lines
        yield line_no, cleaned


def to_int(value: str, what: str, line_no) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Row {line_no}: invalid {what} {value!r}") from None


def to_amount(value: str, line_no) -> float:
    try:
        amount = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        raise ValueError(f"Row {line_no}: invalid amount {value!r}") from None
    if amount < 0:
        raise ValueError(f"Row {line_no}: amount cannot be negative ({value})")
    return amount


def need(row: dict, key: str, line_no) -> str:
    value = row.get(key, "")
    if not value:
        raise ValueError(f"Row {line_no}: '{key}' is empty")
    return value


def normalize_code(conn: sqlite3.Connection, bank: str, raw_code: str) -> str:
    row = conn.execute(
        "SELECT canonical_code FROM bank_code_mapping WHERE sponsor_bank=? AND raw_code=?",
        (bank, raw_code),
    ).fetchone()
    if row:
        return row[0]

    # Free-text fallback (Axis files carry a sentence, not a code): substring match.
    if raw_code.startswith("FREE_TEXT:"):
        text = raw_code[len("FREE_TEXT:"):]
        for r in conn.execute(
            "SELECT raw_code, canonical_code FROM bank_code_mapping "
            "WHERE sponsor_bank=? AND raw_code LIKE 'FREE_TEXT:%'", (bank,)
        ):
            if r[0][len("FREE_TEXT:"):] in text:
                return r[1]
    return "UNMAPPED"


def insert_raw(conn, *, instrument, bank, primary, secondary, seq, return_date,
               raw_code, raw_desc, account, customer, amount, batch_id,
               fingerprint_seq=None) -> bool:
    """Insert one raw row. Returns False if an identical row already exists."""
    fingerprint = row_hash(
        instrument, bank, primary, secondary,
        seq if fingerprint_seq is None else fingerprint_seq,
        return_date, raw_code, raw_desc, account, customer, amount,
    )
    if conn.execute("SELECT 1 FROM raw_returns WHERE row_hash=?", (fingerprint,)).fetchone():
        return False
    conn.execute(
        """INSERT INTO raw_returns (
              instrument_type, sponsor_bank, primary_ref, secondary_ref,
              presentation_seq, return_date, raw_code, raw_description,
              account_no, customer_name, amount, canonical_code, dedup_key,
              row_hash, import_batch_id
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (instrument, bank, primary, secondary, seq, return_date, raw_code,
         raw_desc, account, customer, amount,
         normalize_code(conn, bank, raw_code),
         f"{instrument}|{primary}|{secondary}", fingerprint, batch_id),
    )
    return True


# --------------------------------------------------------------------------
# Parsers: each returns the number of source rows read
# --------------------------------------------------------------------------

def parse_hdfc_csv(conn, data: bytes, batch_id: int) -> int:
    required = ["UMRN", "PresentationSeqNo", "DueDate", "ReturnDate", "ReturnCode",
                "AccountNo", "CustomerName", "Amount"]
    rows = 0
    for n, r in read_rows(data, required):
        insert_raw(conn, instrument="NACH", bank="HDFC", primary=need(r, "umrn", n),
                   secondary=need(r, "duedate", n),
                   seq=to_int(r["presentationseqno"], "PresentationSeqNo", n),
                   return_date=need(r, "returndate", n), raw_code=need(r, "returncode", n),
                   raw_desc=r.get("returnreasontext", ""), account=need(r, "accountno", n),
                   customer=need(r, "customername", n), amount=to_amount(r["amount"], n),
                   batch_id=batch_id)
        rows += 1
    return rows


def parse_icici_pipe(conn, data: bytes, batch_id: int) -> int:
    required = ["MandateUMRN", "PresentmentNo", "CollectionDate", "ReturnDate", "RtnCode",
                "CustAccountNo", "CustName", "TxnAmount"]
    rows = 0
    for n, r in read_rows(data, required, delimiter="|"):
        insert_raw(conn, instrument="NACH", bank="ICICI", primary=need(r, "mandateumrn", n),
                   secondary=need(r, "collectiondate", n),
                   seq=to_int(r["presentmentno"], "PresentmentNo", n),
                   return_date=need(r, "returndate", n), raw_code=need(r, "rtncode", n),
                   raw_desc=r.get("rtndesc", ""), account=need(r, "custaccountno", n),
                   customer=need(r, "custname", n), amount=to_amount(r["txnamount"], n),
                   batch_id=batch_id)
        rows += 1
    return rows


def parse_axis_xml(conn, data: bytes, batch_id: int) -> int:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from None
    except Exception as exc:  # defusedxml raises for forbidden constructs
        raise ValueError(f"XML rejected: {exc}") from None

    rows = 0
    for n, rec in enumerate(root.findall("ReturnRecord"), start=1):
        def text(tag, default=""):
            return (rec.findtext(tag, default) or "").strip()

        reason = text("ReasonText")
        if not text("UMRN") or not reason:
            raise ValueError(f"Record {n}: UMRN and ReasonText are required")
        insert_raw(conn, instrument="NACH", bank="AXIS", primary=text("UMRN"),
                   secondary=text("DueDate"),
                   seq=to_int(text("PresentationAttempt", "1"), "PresentationAttempt", n),
                   return_date=text("ReturnDate"),
                   raw_code=f"FREE_TEXT:{reason.lower()}", raw_desc=reason,
                   account=text("AccountNumber"), customer=text("CustomerName"),
                   amount=to_amount(text("Amount", "0"), n), batch_id=batch_id)
        rows += 1
    if rows == 0:
        raise ValueError("No <ReturnRecord> elements found")
    return rows


def parse_sbi_cheque_csv(conn, data: bytes, batch_id: int) -> int:
    """Cheque files carry no presentation sequence, so the attempt number is
    assigned later (in rebuild_derived) from the return dates. The presentation
    date is used in the row fingerprint instead, so the same row appearing in
    two overlapping files is not counted twice."""
    required = ["ChequeNumber", "DraweeAccountNo", "PayeeName", "Amount", "ReturnCode",
                "ReturnDate"]
    rows = 0
    for n, r in read_rows(data, required):
        cheque, account = need(r, "chequenumber", n), need(r, "draweeaccountno", n)
        insert_raw(conn, instrument="CHEQUE", bank="SBI", primary=cheque, secondary=account,
                   seq=1, fingerprint_seq=r.get("presentationdate", ""),
                   return_date=need(r, "returndate", n), raw_code=need(r, "returncode", n),
                   raw_desc=r.get("returnreasontext", ""), account=account,
                   customer=need(r, "payeename", n), amount=to_amount(r["amount"], n),
                   batch_id=batch_id)
        rows += 1
    return rows


Parser = Callable[[sqlite3.Connection, bytes, int], int]


def detect_file(data: bytes, filename: str) -> tuple[str, str, Parser]:
    """Pick a parser. File content wins; the file name is only a fallback."""
    lower = filename.lower()
    stripped = data.lstrip()
    if stripped.startswith(b"<?xml") or lower.endswith(".xml") or b"<NACHReturnFile" in stripped:
        return "AXIS", "NACH", parse_axis_xml

    text = decode_text(data[:4000])
    lines = text.splitlines()
    first = lines[0] if lines else ""
    csv_headers = {x.strip().lower() for x in first.split(",")}
    pipe_headers = {x.strip().lower() for x in first.split("|")}

    if {"chequenumber", "draweeaccountno"} & csv_headers:
        return "SBI", "CHEQUE", parse_sbi_cheque_csv
    if {"umrn", "presentationseqno"} <= csv_headers:
        return "HDFC", "NACH", parse_hdfc_csv
    if {"mandateumrn", "presentmentno"} <= pipe_headers:
        return "ICICI", "NACH", parse_icici_pipe

    raise ValueError(
        "Unsupported file format. Expected HDFC CSV, ICICI pipe TXT/CSV, "
        "AXIS XML or SBI cheque CSV (check the header row)."
    )


# --------------------------------------------------------------------------
# Derived tables
# --------------------------------------------------------------------------

def rebuild_derived(conn: sqlite3.Connection) -> None:
    """Rebuild events, attempts and charges from the raw rows (audit layer)."""
    conn.execute("DELETE FROM charges")
    conn.execute("DELETE FROM presentation_attempts")
    conn.execute("DELETE FROM bounce_events")

    event_ids: dict[str, int] = {}
    attempt_no: dict[str, int] = {}
    rows = conn.execute("""
        SELECT id, instrument_type, sponsor_bank, primary_ref, secondary_ref,
               presentation_seq, return_date, account_no, customer_name,
               amount, canonical_code, dedup_key
        FROM raw_returns
        ORDER BY dedup_key,
                 CASE WHEN instrument_type='CHEQUE' THEN 0 ELSE presentation_seq END,
                 return_date, id
    """).fetchall()

    for r in rows:
        key = r["dedup_key"]
        attempt_no[key] = attempt_no.get(key, 0) + 1
        if key not in event_ids:
            cur = conn.execute("""
                INSERT INTO bounce_events (
                    instrument_type, primary_ref, secondary_ref, dedup_key,
                    account_no, customer_name, amount, canonical_code,
                    sponsor_bank, attempt_count
                ) VALUES (?,?,?,?,?,?,?,?,?,1)
            """, (r["instrument_type"], r["primary_ref"], r["secondary_ref"], key,
                  r["account_no"], r["customer_name"], r["amount"],
                  r["canonical_code"], r["sponsor_bank"]))
            event_ids[key] = cur.lastrowid
        else:
            conn.execute("UPDATE bounce_events SET attempt_count=attempt_count+1 WHERE id=?",
                         (event_ids[key],))

        seq = r["presentation_seq"] if r["instrument_type"] == "NACH" else attempt_no[key]
        conn.execute("""
            INSERT INTO presentation_attempts
              (bounce_event_id, raw_return_id, presentation_seq, return_date)
            VALUES (?,?,?,?)
        """, (event_ids[key], r["id"], seq, r["return_date"]))

    for r in conn.execute("""
        SELECT be.id, be.instrument_type, be.primary_ref, be.secondary_ref,
               be.customer_name, be.account_no, be.amount, be.attempt_count,
               m.display_name, m.is_chargeable
        FROM bounce_events be
        LEFT JOIN master_reason_codes m ON be.canonical_code=m.canonical_code
    """).fetchall():
        if not r["display_name"] or r["is_chargeable"] != "Y":
            continue
        fee = f"Rs.{BOUNCE_CHARGE_AMOUNT:.2f}"
        if r["instrument_type"] == "NACH":
            notice = (f"Dear {r['customer_name']}, your NACH mandate (UMRN {r['primary_ref']}) "
                      f"for Rs.{r['amount']:.2f} due on {r['secondary_ref']} was returned due to "
                      f"'{r['display_name']}'. A bounce charge of {fee} has been applied. "
                      f"This mandate was presented {r['attempt_count']} time(s) in this cycle; "
                      f"only one charge raised.")
        else:
            notice = (f"Dear {r['customer_name']}, your cheque number {r['primary_ref']} for "
                      f"Rs.{r['amount']:.2f} drawn on account {r['secondary_ref']} was returned "
                      f"due to '{r['display_name']}'. A cheque bounce charge of {fee} has been "
                      f"applied. This cheque was presented {r['attempt_count']} time(s); "
                      f"only one charge has been raised.")
        conn.execute("""
            INSERT INTO charges
              (bounce_event_id, instrument_type, primary_ref, customer_name,
               account_no, reason, charge_amount, notice_text)
            VALUES (?,?,?,?,?,?,?,?)
        """, (r["id"], r["instrument_type"], r["primary_ref"], r["customer_name"],
              r["account_no"], r["display_name"], BOUNCE_CHARGE_AMOUNT, notice))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def upload_file(conn: sqlite3.Connection, filename: str, data: bytes) -> dict:
    """Process one uploaded file. Never raises for bad input; returns a result
    dict whose ``status`` is ``success``, ``duplicate`` or ``failed``."""
    filename = os.path.basename((filename or "").replace("\\", "/")).strip()
    result = {"file_name": filename}

    if not filename:
        return {**result, "status": "failed", "message": "A file name is required."}
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        return {**result, "status": "failed",
                "message": "Only .csv, .txt and .xml files are accepted."}
    if not data:
        return {**result, "status": "failed", "message": "The uploaded file is empty."}
    if len(data) > MAX_UPLOAD_BYTES:
        return {**result, "status": "failed", "message": "File is larger than the 20 MB limit."}

    file_hash = sha256_bytes(data)
    previous = conn.execute(
        "SELECT id, file_name, status FROM import_batches WHERE file_hash=?", (file_hash,)
    ).fetchone()
    if previous and previous["status"] == "SUCCESS":
        return {**result, "status": "duplicate", "batch_id": previous["id"], "rows_added": 0,
                "message": f"This exact file was already uploaded as {previous['file_name']}."}
    if previous:  # an earlier attempt failed; allow a retry
        conn.execute("DELETE FROM import_batches WHERE id=?", (previous["id"],))

    cur = conn.execute(
        "INSERT INTO import_batches (file_name,file_hash,row_count,status,uploaded_at) "
        "VALUES (?,?,0,'PROCESSING',?)",
        (filename, file_hash, datetime.now(timezone.utc).isoformat()),
    )
    batch_id = cur.lastrowid
    conn.commit()

    try:
        bank, instrument, parser = detect_file(data, filename)
        source_rows = parser(conn, data, batch_id)
        conn.execute(
            "UPDATE import_batches SET row_count=?, status='SUCCESS', sponsor_bank=?, "
            "instrument_type=? WHERE id=?", (source_rows, bank, instrument, batch_id))
        rebuild_derived(conn)
        conn.commit()
    except (ValueError, sqlite3.Error) as exc:
        conn.rollback()
        conn.execute("UPDATE import_batches SET status='FAILED', error_message=? WHERE id=?",
                     (str(exc), batch_id))
        conn.commit()
        return {**result, "status": "failed", "batch_id": batch_id, "message": str(exc)}

    added = conn.execute("SELECT COUNT(*) FROM raw_returns WHERE import_batch_id=?",
                         (batch_id,)).fetchone()[0]
    return {
        **result, "status": "success", "message": f"Processed {filename} successfully.",
        "batch_id": batch_id, "bank": bank, "instrument_type": instrument,
        "source_rows": source_rows, "rows_added": added,
        "unique_events": conn.execute("SELECT COUNT(*) FROM bounce_events").fetchone()[0],
        "charges": conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0],
        "charge_total": conn.execute(
            "SELECT COALESCE(SUM(charge_amount),0) FROM charges").fetchone()[0],
    }


def upload_files(conn: sqlite3.Connection, files: Iterable[tuple[str, bytes]]) -> list[dict]:
    """Process several files; one bad file does not stop the others."""
    return [upload_file(conn, name, data) for name, data in files]
