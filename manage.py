#!/usr/bin/env python3
"""Command-line helper for the NACH & Cheque Return Tracker.

    python manage.py init                 create the database (and seed reference data)
    python manage.py load FILE [FILE...]  process return files without starting the server
    python manage.py report               print a summary report from the database
    python manage.py reset [--yes]        delete all uploaded data (keeps reference tables)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import database
from process_uploads import upload_files


def cmd_init(_args) -> int:
    conn = database.connect()
    database.init_db(conn)
    print(f"Database ready at {database.get_db_path()}")
    return 0


def cmd_load(args) -> int:
    conn = database.connect()
    database.init_db(conn)
    files = []
    for name in args.files:
        p = Path(name)
        if not p.is_file():
            print(f"  [skip] {name}: not a file")
            continue
        files.append((p.name, p.read_bytes()))
    exit_code = 0
    for r in upload_files(conn, files):
        if r["status"] == "success":
            print(f"  [ok]        {r['file_name']}: {r['source_rows']} rows read, {r['rows_added']} new")
        elif r["status"] == "duplicate":
            print(f"  [duplicate] {r['file_name']}: already uploaded")
        else:
            exit_code = 1
            print(f"  [FAILED]    {r['file_name']}: {r['message']}")
    return exit_code


def cmd_reset(args) -> int:
    if not args.yes and input("Delete ALL uploaded data? Type 'yes' to continue: ").strip() != "yes":
        print("Cancelled.")
        return 1
    conn = database.connect()
    database.init_db(conn)
    database.reset_uploaded_data(conn)
    print("Uploaded data deleted. Reference tables kept.")
    return 0


def _section(title: str) -> None:
    print("\n" + "=" * 62 + f"\n{title}\n" + "=" * 62)


def cmd_report(_args) -> int:
    path = database.get_db_path()
    if not Path(path).exists():
        print(f"No database at {path}. Run 'python manage.py init' or start the app first.")
        return 1
    conn = database.connect()
    q = lambda sql, *p: conn.execute(sql, p).fetchall()  # noqa: E731

    _section("1. Bounce events by instrument type")
    for r in q("SELECT instrument_type, COUNT(*) FROM bounce_events GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r[0]:8s}: {r[1]}")

    _section("2. Bounce events by sponsor bank")
    for r in q("SELECT sponsor_bank, instrument_type, COUNT(*) FROM bounce_events GROUP BY 1, 2"):
        print(f"  {r[0]:8s} ({r[1]}): {r[2]}")

    _section("3. Top reasons (canonical)")
    for r in q("SELECT canonical_code, COUNT(*) FROM bounce_events GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r[0]:28s}: {r[1]}")

    _section("4. Charges raised")
    total, count = q("SELECT COALESCE(SUM(charge_amount),0), COUNT(*) FROM charges")[0]
    print(f"  {count} charge(s) totalling Rs.{total:.2f}")
    for r in q("SELECT instrument_type, COUNT(*), SUM(charge_amount) FROM charges GROUP BY 1"):
        print(f"    - {r[0]:8s}: {r[1]} charge(s), Rs.{r[2]:.2f}")

    _section("5. Duplicate re-presentations suppressed")
    raw = q("SELECT COUNT(*) FROM raw_returns")[0][0]
    events = q("SELECT COUNT(*) FROM bounce_events")[0][0]
    print(f"  {raw} raw rows -> {events} unique events ({raw - events} suppressed)")

    _section("6. Re-presented more than once (charged once)")
    for r in q("""SELECT instrument_type, primary_ref, customer_name, sponsor_bank, attempt_count
                  FROM bounce_events WHERE attempt_count > 1 ORDER BY attempt_count DESC"""):
        print(f"  {r[0]} {r[1]} ({r[2]}, {r[3]}): presented {r[4]} times")

    _section("7. Needs manual review (unmapped bank codes)")
    rows = q("""SELECT sponsor_bank, raw_code, COUNT(*) FROM raw_returns
                WHERE canonical_code='UNMAPPED' GROUP BY 1, 2""")
    for r in rows:
        print(f"  {r[0]}: code {r[1]!r} x{r[2]}")
    if not rows:
        print("  none")

    _section("8. Sample notices")
    for instr in ("NACH", "CHEQUE"):
        row = q("SELECT notice_text FROM charges WHERE instrument_type=? LIMIT 1", instr)
        if row:
            print(f"\n  [{instr}] {row[0][0]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("report").set_defaults(func=cmd_report)
    p = sub.add_parser("load")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_load)
    p = sub.add_parser("reset")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(func=cmd_reset)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
