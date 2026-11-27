# Sample mappings

Working DataForge mappings that read CSV and flat files and write CSV and flat files. They are published with the
site, so anyone can open them from **Load Sample ▸ From the samples folder** at
https://theraghavraman.github.io/dataforge/ without uploading anything.

| Folder | Holds |
| --- | --- |
| `source/` | Input files the mappings read |
| `target/` | Output each mapping writes, checked by `npm run test:samples` |
| `mappings/` | Mapping files (DataForge mapping JSON, version 1.0) |
| `catalog.json` | The list shown in the Load Sample menu |

## The mappings

| Mapping | Reads (`source/`) | Writes (`target/`) | Shows |
| --- | --- | --- | --- |
| Customer cleanse · CSV to CSV | `customers.csv` | `customers_clean.csv`, `customers_rejects.csv` | Trim and case fixes, phone clean-up, Data Quality rejects and flags, filter, sort |
| Sales by category · flat file to CSV | `orders.txt` (pipe-delimited), `products.csv` | `order_lines.txt` (pipe-delimited), `sales_by_category.csv`, `orders_unknown_product.csv` | Flat-file source, lookup with rejects, aggregation, flat-file target |
| Employee payroll · CSV to flat file | `employees.csv` (semicolon-delimited) | `payroll_extract.txt` (pipe-delimited), `department_summary.csv` | Non-comma delimiter, `$$PAY_PERIOD` and `$$AS_OF_DATE` parameters, two targets |
| Account summary · fixed-width flat file | `transactions_fixed_width.txt` | `transactions_parsed.csv`, `account_summary.csv` | Cutting fields by position, signed amounts, per-account balances |

`transactions_fixed_width.txt` has no delimiter. Each 62-character line is read as one text column (`column0`) and cut by
position:

| Field | Positions | Example |
| --- | --- | --- |
| `account_no` | 1–10 | `ACC1001   ` |
| `txn_date` | 11–18 (YYYYMMDD) | `20251103` |
| `txn_type` | 19–20 (`CR` or `DR`) | `DR` |
| `amount` | 21–32 (cents, zero-padded) | `000000012550` = 125.50 |
| `description` | 33–62 | `Grocery store` |

## Use them

- **On the website:** choose **Load Sample ▸ From the samples folder**, pick a mapping, then **Run**. The source files
  are fetched from `source/` and remembered in your browser, so the mapping still runs after a reload. Each target
  node offers its output for download; compare it with the file of the same name in `target/`.
- **Locally:** `node scripts/serve.mjs` serves this folder too. You can also open a file from `mappings/` with
  **Open** and upload its sources from `source/` yourself.

## Add a mapping

1. Put its input files in `source/`.
2. Build and run it in the studio, then **Save** it as JSON into `mappings/`. Source file names must match the files in
   `source/`.
3. Add an entry to `catalog.json`.
4. Run `npm run test:samples -- --write` to write its outputs into `target/`, check them, and commit everything.
   `npm run check:samples` and `npm run test:samples` then keep it working; the Pages workflow runs both before it
   deploys.

Sample data is made up. File names may use letters, digits, `.`, `_` and `-`; each file must stay under 5 MB.
