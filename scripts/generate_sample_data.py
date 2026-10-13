#!/usr/bin/env python3
"""Generate larger *fictional* return files for load-testing the upload feature.

All names, account numbers and amounts are random mock data. Output is
deterministic (fixed seed), so re-running gives identical files.

    python scripts/generate_sample_data.py [output_dir]

Default output: sample_uploads/04_bulk/
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path
from xml.sax.saxutils import escape

SEED = 20261010
FIRST = ["Aarav", "Anika", "Bhavya", "Chirag", "Diya", "Esha", "Farhan", "Gauri", "Hemant",
         "Isha", "Jatin", "Kavya", "Lokesh", "Mira", "Naveen", "Omkar", "Preeti", "Qasim",
         "Rhea", "Sameer", "Tanvi", "Uday", "Vidya", "Yash", "Zoya"]
LAST = ["Agarwal", "Banerjee", "Chopra", "Dutta", "Fernandes", "Gill", "Hegde", "Joshi",
        "Khanna", "Lal", "Mehta", "Nair", "Oberoi", "Patil", "Rao", "Sinha", "Thakur",
        "Varma", "Wadhwa"]

HDFC_CODES = [("01", "Account closed"), ("03", "Insufficient funds"),
              ("21", "Invalid UMRN / mandate not active"), ("27", "Mandate amount mismatch")]
ICICI_CODES = [("IF02", "Funds Insufficient in account"), ("AC09", "Account Closed by customer"),
               ("MN14", "Mandate Not Active / Not Found")]
AXIS_REASONS = ["Insufficient balance in customer account", "Customer account is closed / inoperative",
                "Mandate reference not found or expired",
                "Destination bank technical error"]  # last one is deliberately unmapped
SBI_CODES = [("01", "Funds Insufficient"), ("02", "Account Closed"), ("03", "Payment Stopped by Drawer"),
             ("07", "Signature Differs"), ("34", "Amount in Words and Figures Differs"),
             ("35", "Cheque Post Dated / Stale")]


def day(base: int, offset: int) -> str:
    return f"2026-09-{base + offset:02d}"


def person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}"


def attempts(rng: random.Random) -> int:
    return rng.choices([1, 2, 3], weights=[70, 22, 8])[0]


def gen_hdfc(path: Path, n: int, rng: random.Random) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["UMRN", "PresentationSeqNo", "DueDate", "ReturnDate", "ReturnCode",
                    "ReturnReasonText", "AccountNo", "CustomerName", "Amount"])
        for i in range(n):
            umrn, acct, name = f"HDFC0002{i:09d}", f"5020{i:010d}", person(rng)
            amount = f"{rng.randrange(500, 40000, 50):.2f}"
            code, text = rng.choice(HDFC_CODES)
            for seq in range(1, attempts(rng) + 1):
                w.writerow([umrn, seq, "2026-09-17", day(18, seq * 2), code, text, acct, name, amount])


def gen_icici(path: Path, n: int, rng: random.Random) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("MandateUMRN|PresentmentNo|CollectionDate|ReturnDate|RtnCode|RtnDesc|CustAccountNo|CustName|TxnAmount\n")
        for i in range(n):
            umrn, acct, name = f"ICIC0003{i:09d}", f"6030{i:010d}", person(rng)
            amount = f"{rng.randrange(500, 40000, 50):.2f}"
            code, text = rng.choice(ICICI_CODES)
            for seq in range(1, attempts(rng) + 1):
                f.write("|".join([umrn, str(seq), "2026-09-17", day(18, seq * 2), code, text, acct, name, amount]) + "\n")


def gen_axis(path: Path, n: int, rng: random.Random) -> None:
    out = ["<?xml version='1.0' encoding='utf-8'?>", '<NACHReturnFile bank="AXIS" cycleDate="2026-09-17">']
    for i in range(n):
        umrn, acct, name = f"UTIB0004{i:09d}", f"9104{i:010d}", person(rng)
        amount = f"{rng.randrange(500, 40000, 50):.2f}"
        reason = rng.choice(AXIS_REASONS)
        for seq in range(1, attempts(rng) + 1):
            out += ["  <ReturnRecord>", f"    <UMRN>{umrn}</UMRN>",
                    f"    <PresentationAttempt>{seq}</PresentationAttempt>",
                    "    <DueDate>2026-09-17</DueDate>", f"    <ReturnDate>{day(18, seq * 2)}</ReturnDate>",
                    f"    <ReasonText>{escape(reason)}</ReasonText>",
                    f"    <AccountNumber>{acct}</AccountNumber>", f"    <CustomerName>{escape(name)}</CustomerName>",
                    f"    <Amount>{amount}</Amount>", "  </ReturnRecord>"]
    out.append("</NACHReturnFile>")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def gen_sbi(path: Path, n: int, rng: random.Random) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ChequeNumber", "DraweeAccountNo", "PayeeName", "Amount", "ReturnCode",
                    "ReturnReasonText", "PresentationDate", "ReturnDate"])
        for i in range(n):
            cheque, acct, name = f"{200000 + i}", f"3040{i:09d}", person(rng)
            amount = f"{rng.randrange(1000, 90000, 100):.2f}"
            code, text = rng.choice(SBI_CODES)
            for k in range(attempts(rng)):
                w.writerow([cheque, acct, name, amount, code, text, day(17, k * 3), day(18, k * 3)])


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "sample_uploads" / "04_bulk"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    gen_hdfc(out / "hdfc_bulk_returns.csv", 250, rng)
    gen_icici(out / "icici_bulk_returns.txt", 150, rng)
    gen_axis(out / "axis_bulk_returns.xml", 100, rng)
    gen_sbi(out / "sbi_bulk_cheque_returns.csv", 120, rng)
    for p in sorted(out.iterdir()):
        print(f"  {p.name}: {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
