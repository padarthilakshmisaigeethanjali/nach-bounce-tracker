"""End-to-end tests of the upload feature using the files in sample_uploads/."""
import io
import json
import pytest

BASELINE = [
    "01_baseline/hdfc_returns_20260910.csv",
    "01_baseline/icici_returns_20260910.txt",
    "01_baseline/axis_returns_20260910.xml",
    "01_baseline/sbi_cheque_returns_20260910.csv",
]
NEXT_CYCLE = [
    "02_next_cycle/hdfc_returns_20261010.csv",
    "02_next_cycle/icici_returns_20261010.txt",
    "02_next_cycle/axis_returns_20261010.xml",
    "02_next_cycle/sbi_cheque_returns_20261010.csv",
]


def summary(client):
    return client.get("/api/summary").get_json()


def find_event(client, ref):
    rows = client.get(f"/api/events?q={ref}").get_json()
    assert len(rows) == 1, rows
    return rows[0]


def test_empty_database_works(client):
    s = summary(client)
    assert s["raw_returns"] == 0 and s["charges"] == 0
    assert client.get("/api/health").get_json()["status"] == "ok"
    assert client.get("/").status_code == 200


def test_baseline_upload(client, upload):
    results = upload(*BASELINE)
    assert [r["status"] for r in results] == ["success"] * 4
    assert [r["bank"] for r in results] == ["HDFC", "ICICI", "AXIS", "SBI"]
    s = summary(client)
    assert s["raw_returns"] == 18
    assert s["unique_events"] == 13
    assert s["duplicates_suppressed"] == 5
    assert s["charges"] == 8 and s["charge_total"] == 2000
    assert s["needs_review"] == 0


def test_re_presentation_charged_once(client, upload):
    upload(*BASELINE)
    ev = find_event(client, "ICIC0009876543211")
    assert ev["attempt_count"] == 3 and ev["charged"] == 1 and ev["charge_amount"] == 250
    detail = client.get(f"/api/events/{ev['id']}").get_json()
    assert [a["presentation_seq"] for a in detail["attempts"]] == [1, 2, 3]
    assert "presented 3 time(s)" in detail["notice_text"]


def test_non_chargeable_reason_not_charged(client, upload):
    upload(*BASELINE)
    ev = find_event(client, "HDFC0001234567892")  # mandate not registered
    assert ev["charged"] == 0


def test_same_file_twice_is_duplicate(client, upload):
    upload(BASELINE[0])
    before = summary(client)
    again = upload(BASELINE[0])[0]
    assert again["status"] == "duplicate" and again["file_name"] == "hdfc_returns_20260910.csv"
    assert summary(client) == before


def test_next_cycle_adds_new_events(client, upload):
    upload(*BASELINE)
    assert all(r["status"] == "success" for r in upload(*NEXT_CYCLE))
    s = summary(client)
    assert s["unique_events"] == 13 + 10
    # Kiran Bedi's cheque presented three times -> one event, one charge
    ev = find_event(client, "000901")
    assert ev["attempt_count"] == 3 and ev["charged"] == 1


def test_header_detection_beats_filename_and_handles_bom(client, upload):
    r = upload("03_edge_cases/returns_batch_07_renamed_bom.csv")[0]
    assert r["status"] == "success" and r["bank"] == "HDFC"


def test_unmapped_code_flagged_not_charged(client, upload):
    upload("03_edge_cases/hdfc_unmapped_code.csv")
    ev = find_event(client, "HDFC0001234567921")
    assert ev["canonical_code"] == "UNMAPPED" and ev["charged"] == 0
    assert summary(client)["needs_review"] == 1
    assert find_event(client, "HDFC0001234567922")["charged"] == 1


def test_late_representation_in_later_file(client, upload):
    upload(BASELINE[0])
    assert find_event(client, "HDFC0001234567891")["attempt_count"] == 2
    upload("03_edge_cases/hdfc_late_representation.csv")
    ev = find_event(client, "HDFC0001234567891")
    assert ev["attempt_count"] == 3
    assert summary(client)["charges"] == 2  # still one charge per event (two chargeable HDFC events)


def test_overlapping_cheque_files_do_not_double_count(client, upload):
    upload(BASELINE[3])
    assert find_event(client, "000123")["attempt_count"] == 2
    r = upload("03_edge_cases/sbi_cheque_overlap.csv")[0]
    assert r["source_rows"] == 3 and r["rows_added"] == 2  # exact repeat skipped
    assert find_event(client, "000123")["attempt_count"] == 3
    detail = client.get(f"/api/events/{find_event(client, '000123')['id']}").get_json()
    assert [a["presentation_seq"] for a in detail["attempts"]] == [1, 2, 3]


@pytest.mark.parametrize("rel,fragment", [
    ("03_edge_cases/bad_missing_column.csv", "ReturnCode"),
    ("03_edge_cases/bad_amount.csv", "Row 3"),
    ("03_edge_cases/unsupported_format.csv", "Unsupported file format"),
    ("03_edge_cases/empty.csv", "empty"),
    ("03_edge_cases/malformed.xml", "Invalid XML"),
    ("03_edge_cases/xml_entity_attack.xml", "XML rejected"),
    ("03_edge_cases/wrong_extension.json", "Only .csv"),
])
def test_bad_files_fail_cleanly(client, upload, rel, fragment):
    r = upload(rel)[0]
    assert r["status"] == "failed", r
    assert fragment.lower() in r["message"].lower()
    assert summary(client)["raw_returns"] == 0  # nothing partially imported


def test_one_bad_file_does_not_block_the_others(client, upload):
    results = upload(BASELINE[0], "03_edge_cases/bad_amount.csv", BASELINE[1])
    assert [r["status"] for r in results] == ["success", "failed", "success"]
    assert summary(client)["raw_returns"] == 9


def test_failed_file_can_be_retried_after_fix(client, conn):
    from process_uploads import upload_file
    bad = b"UMRN,PresentationSeqNo,DueDate,ReturnDate,ReturnCode,AccountNo,CustomerName,Amount\nX,1,d,d,03,1,N,abc\n"
    assert upload_file(conn, "a.csv", bad)["status"] == "failed"
    assert upload_file(conn, "a.csv", bad)["status"] == "failed"  # not reported as "duplicate"


def test_history_and_charges_endpoints(client, upload):
    upload("03_edge_cases/bad_amount.csv", BASELINE[0])
    hist = client.get("/api/uploads").get_json()
    assert {h["status"] for h in hist} == {"SUCCESS", "FAILED"}
    charges = client.get("/api/charges").get_json()
    assert len(charges) == 2 and all("Rs.250.00" in c["notice_text"] for c in charges)


def test_filters(client, upload):
    upload(*BASELINE)
    assert len(client.get("/api/events?instrument=CHEQUE").get_json()) == 5
    assert len(client.get("/api/events?bank=hdfc").get_json()) == 3
    assert len(client.get("/api/events?reason=ACCOUNT_CLOSED").get_json()) == 3
    assert len(client.get("/api/events?q=Ramesh").get_json()) == 1
    assert len(client.get("/api/events?limit=2").get_json()) == 2
    assert client.get("/api/events/9999").status_code == 404


def test_no_files_is_400(client):
    assert client.post("/api/uploads", data={}).status_code == 400


def test_bulk_files(client, upload):
    results = upload(*[f"04_bulk/{n}" for n in (
        "hdfc_bulk_returns.csv", "icici_bulk_returns.txt",
        "axis_bulk_returns.xml", "sbi_bulk_cheque_returns.csv")])
    assert all(r["status"] == "success" for r in results)
    s = summary(client)
    assert s["unique_events"] == 250 + 150 + 100 + 120
    assert s["raw_returns"] > s["unique_events"] and s["needs_review"] > 0
    assert len(client.get("/api/events").get_json()) == 500  # default page size


def test_xss_is_data_not_markup(client):
    csv = ("ChequeNumber,DraweeAccountNo,PayeeName,Amount,ReturnCode,ReturnReasonText,PresentationDate,ReturnDate\n"
           '777,1,"<script>alert(1)</script>",10,01,x,2026-01-01,2026-01-02\n').encode()
    r = client.post("/api/uploads", data={"files": (io.BytesIO(csv), "x.csv")}).get_json()["results"][0]
    assert r["status"] == "success"
    # API returns it verbatim as JSON; the frontend escapes on render.
    assert client.get("/api/events?q=777").get_json()[0]["customer_name"] == "<script>alert(1)</script>"


def test_reset(conn):
    import database
    from process_uploads import upload_file
    from conftest import sample
    n, d = sample(BASELINE[0])
    upload_file(conn, n, d)
    database.reset_uploaded_data(conn)
    assert conn.execute("SELECT COUNT(*) FROM raw_returns").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM master_reason_codes").fetchone()[0] > 0
    assert upload_file(conn, n, d)["status"] == "success"  # can re-upload after reset
