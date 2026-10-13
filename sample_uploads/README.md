# Sample upload files

All data here is **fictional mock data** (invented names, account numbers and
amounts). Use these files to try the upload feature in the dashboard
(drag them onto the upload box) or the API:

```bash
curl -X POST http://localhost:5000/api/uploads -F "files=@sample_uploads/01_baseline/hdfc_returns_20260910.csv"
```

Tip: upload `01_baseline` first, then the others. Reset between runs with
`python manage.py reset --yes`.

## 01_baseline — the four supported formats
| File | Format | Notes |
|---|---|---|
| `hdfc_returns_20260910.csv` | HDFC NACH CSV | one mandate presented twice |
| `icici_returns_20260910.txt` | ICICI pipe-delimited | one mandate presented 3 times |
| `axis_returns_20260910.xml` | AXIS XML | free-text reasons, mapped by substring |
| `sbi_cheque_returns_20260910.csv` | SBI cheque CSV | one cheque re-deposited |

**Expected after all four:** 18 raw rows → 13 unique events, 5 duplicates
suppressed, 8 charges = ₹2,000.

## 02_next_cycle — a second cycle (October)
Four new files with new customers. Uploading them after the baseline gives
23 unique events. Cheque `000901` is presented three times but charged once.

## 03_edge_cases — what should happen with awkward input
| File | Expected result |
|---|---|
| `returns_batch_07_renamed_bom.csv` | **Success** — HDFC detected from the header row (file name has no bank hint; UTF-8 BOM) |
| `hdfc_unmapped_code.csv` | **Success** — code `99` is stored as `UNMAPPED`, not charged, flagged "needs manual review" |
| `hdfc_late_representation.csv` | **Success** — attempt 3 of a baseline mandate; event goes from 2 → 3 attempts, still one charge |
| `sbi_cheque_overlap.csv` | **Success** — 3 rows read, 2 new (first row is an exact repeat and is skipped) |
| `bad_missing_column.csv` | **Failed** — "Missing required column(s): ReturnCode" |
| `bad_amount.csv` | **Failed** — "Row 3: invalid amount '12abc'"; nothing from the file is kept |
| `unsupported_format.csv` | **Failed** — unrecognised header |
| `empty.csv` | **Failed** — empty file |
| `malformed.xml` | **Failed** — invalid XML |
| `xml_entity_attack.xml` | **Failed** — XML with entity definitions is rejected |
| `wrong_extension.json` | **Failed** — only .csv/.txt/.xml accepted |

Uploading any successful file a second time reports **duplicate** and changes nothing.

## 04_bulk — volume testing
620 unique events (with random re-presentations and some unmapped reasons)
across four files. Regenerate (deterministically) with:

```bash
python scripts/generate_sample_data.py
```

These scenarios are all covered by automated tests in `tests/test_uploads.py`.
