# NACH & Cheque Return Tracker

[![CI](https://github.com/padarthilakshmisaigeethanjali/nach-bounce-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/padarthilakshmisaigeethanjali/nach-bounce-tracker/actions)

A small Flask + SQLite dashboard that ingests bank return files for **NACH
mandates** and **cheques**, and solves two practical problems:

1. **Every bank reports reasons differently** (numeric codes, alphanumeric codes, free text).
   Raw codes are normalised to one canonical reason list.
2. **Re-presentations double-count.** A mandate or cheque presented three times
   is still _one_ bounce, so it is charged **once**.

> ⚠️ **All data in this repository is fictional mock data.** Names, account
> numbers, UMRNs and amounts are invented. Do not upload real customer data to
> this demo — see [Production notes](#production-notes).

## Features

- Drag-and-drop upload of one or many files (or use the REST API)
- Automatic format detection from the file's header row
- Reason-code normalisation via editable seed tables (`data/*.csv`)
- Deduplication: NACH by `UMRN + due date`, cheques by `cheque no + drawee account`
- One ₹250 charge per chargeable event, with a generated customer notice
- Idempotent: re-uploading a file, or overlapping rows across files, never double counts
- Unmapped bank codes are kept, not charged, and flagged for manual review
- Per-file error reporting — one bad file doesn't block the rest
- Dashboard: KPIs, charts, searchable/filterable events, charges & notices, upload history

## Supported files

| Bank  | Type   | Format                   | Detected by                               |
| ----- | ------ | ------------------------ | ----------------------------------------- |
| HDFC  | NACH   | CSV                      | header `UMRN, PresentationSeqNo, …`       |
| ICICI | NACH   | pipe-delimited TXT/CSV   | header `MandateUMRN\|PresentmentNo\|…`    |
| AXIS  | NACH   | XML (`<NACHReturnFile>`) | XML content; free-text reasons            |
| SBI   | Cheque | CSV                      | header `ChequeNumber, DraweeAccountNo, …` |

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/padarthilakshmisaigeethanjali/nach-bounce-tracker.git
cd nach-bounce-tracker

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python app.py
```

Open <http://localhost:5000>. The database is created automatically on first
run — there is nothing to set up.

### Try it with the mock data

Drag files from [`sample_uploads/`](sample_uploads/README.md) onto the upload box:

1. `01_baseline/` — one file per supported format (→ 13 events, 8 charges, ₹2,000)
2. `02_next_cycle/` — a second cycle of data
3. `03_edge_cases/` — duplicates, unmapped codes, overlaps, and deliberately broken files
4. `04_bulk/` — ~620 events for volume testing

Each file's expected outcome is documented in `sample_uploads/README.md`.

## REST API

| Method | Endpoint           | Description                                                                          |
| ------ | ------------------ | ------------------------------------------------------------------------------------ |
| `POST` | `/api/uploads`     | Upload files (multipart field `files`, repeatable)                                   |
| `GET`  | `/api/uploads`     | Last 50 uploads and their status                                                     |
| `GET`  | `/api/summary`     | KPIs and chart data                                                                  |
| `GET`  | `/api/events`      | Bounce events. Query: `q`, `instrument`, `bank`, `reason`, `limit` (≤2000), `offset` |
| `GET`  | `/api/events/<id>` | One event with its presentation history and notice                                   |
| `GET`  | `/api/charges`     | Charges and customer notices                                                         |
| `GET`  | `/api/meta`        | Filter options                                                                       |
| `GET`  | `/api/health`      | Health check                                                                         |

```bash
curl -X POST http://localhost:5000/api/uploads \
  -F "files=@sample_uploads/01_baseline/hdfc_returns_20260910.csv" \
  -F "files=@sample_uploads/01_baseline/icici_returns_20260910.txt"
```

The response has one entry per file with `status` = `success`, `duplicate` or
`failed` (plus a `message`), so a bad file never hides the results of the others.

## Command line

```bash
python manage.py load sample_uploads/01_baseline/*   # process files without the server
python manage.py report                                # text report from the database
python manage.py reset --yes                           # delete uploaded data, keep reference tables
```

## Configuration

Environment variables (see [`.env.example`](.env.example)):

| Variable               | Default              | Purpose                                  |
| ---------------------- | -------------------- | ---------------------------------------- |
| `NACH_DB_PATH`         | `./nach_tracker.db`  | SQLite file location                     |
| `BOUNCE_CHARGE_AMOUNT` | `250.00`             | Charge per chargeable event (₹)          |
| `HOST` / `PORT`        | `127.0.0.1` / `5000` | Server bind address                      |
| `FLASK_DEBUG`          | `0`                  | Set `1` for auto-reload while developing |

To add a bank code, edit `data/bank_code_mapping_seed.csv` (new rows are picked
up on next start). To change which reasons are chargeable, edit
`data/master_reason_codes_seed.csv` (`is_chargeable` = `Y`/`N`).

## How it works

```
upload ─► detect format ─► parse ─► raw_returns (audit layer, row-hash dedup)
                                         │
                       rebuild after every successful upload
                                         ▼
              bounce_events ─► presentation_attempts ─► charges (+ notice text)
```

- **`raw_returns`** keeps every source row exactly as received. Each file is
  fingerprinted (SHA-256) and each row hashed, so repeats are ignored.
- **`bounce_events`**, **`presentation_attempts`** and **`charges`** are
  derived and rebuilt deterministically, so files can arrive in any order.
- Cheque files have no attempt number; it is assigned from the return dates.

## Project layout

```
app.py               Flask app (create_app factory) and REST API
process_uploads.py   Parsing, detection, normalisation, dedup, charges
database.py          Schema, connection helper, reference-data seeding
manage.py            CLI: init / load / report / reset
static/              Dashboard (index.html, app.js, styles.css)
data/                Reference-data seeds (reason codes, bank code mapping)
sample_uploads/      Mock files for testing uploads (+ README of expected results)
scripts/             Mock-data generator
tests/               pytest suite
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Production notes

This is a demo. Before using real financial data you would need, at least:
authentication and authorisation, HTTPS, a production WSGI server
(e.g. `gunicorn "app:create_app()"`), a managed database with backups,
antivirus/content scanning of uploads, audit logging, and review of
data-protection obligations for customer and account information.
The built-in XML parser is hardened (`defusedxml`) and the dashboard escapes
all data it renders, but that is not a substitute for the above.

## License

[MIT](LICENSE)
