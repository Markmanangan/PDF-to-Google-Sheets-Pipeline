# PdfToGoogleSheetsDataEntryPipeline

This repository implements a PDF → structured data pipeline that extracts tables from PDF bank statements and writes the results to Google Sheets, an Excel fallback, and an SQL Server database. It includes two operation modes:

- Web upload UI (`run_ui`) — upload a single PDF via a small Flask server.
- Folder trigger watcher (`run_with_trigger`) — automatically process PDF files dropped into an input folder.

This document explains setup, configuration, and usage for both modes, plus database setup and security notes.

## Quick start

Prerequisites:
- Python 3.10–3.13
- An ODBC driver for SQL Server (ODBC Driver 17 or 18)
- (Optional) Google service account JSON to access Google Sheets

Install dependencies (recommended inside a virtualenv):

```powershell
python -m venv .venv
.\\.venv\\Scripts\\activate
python -m pip install -U pip
python -m pip install -r requirements.txt || python -m pip install -e .
```

Note: this project uses the `pyproject.toml` dependency list; you can install with `pip install -e .` in editable mode.

## Configuration

- Service account for Google Sheets (optional for the automated Google Sheets writer): place your service account JSON in the repo root named `pdfengine-4bc5c52f4cb5.json` (do NOT commit it; add to `.gitignore`).
- SQL Server info: you will need a server name and a Windows account with access (or SQL auth credentials if you adapt the helper).

Environment variables / files:
- `.env` (optional) — place any secrets here; this repo explicitly treats the service account JSON as a file.
- `POPPLER_PATH` — optional path to the Poppler `bin` folder when using OCR fallback.
- `TESSERACT_CMD` — optional full path to `tesseract.exe` when using OCR fallback.

Example values for your current setup:
```powershell
setx POPPLER_PATH "C:\Users\HRIS\Downloads\EngineAI\poppler-26.02.0\Library\bin"
setx TESSERACT_CMD "C:\Users\HRIS\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
```

## Database setup (SQL Server)

We provide a script `create_db.sql` at the repository root that creates the target database and table used by the pipeline. Example usage in SSMS:

1. Open SSMS and connect to your server (e.g. `MSI-WILLYPC\\SQLEXPRESS`) using Windows Authentication.
2. Open `create_db.sql` and execute it.

The script will create a database named `PdfPipelineDB` (you can change the name) and a table `dbo.PdfExtractionRecords` with the following columns:

- `Id` INT IDENTITY PRIMARY KEY
- `DocumentName` NVARCHAR(512)
- `SourceFilePath` NVARCHAR(1024)
- `ExtractionTimestamp` DATETIME2
- `ValidationStatus` NVARCHAR(50)
- `ValidationNotes` NVARCHAR(MAX)
- `ExtractedRowsJson` NVARCHAR(MAX)
- `ExcelPath` NVARCHAR(1024)
- `ProcessedAt` DATETIME2

`ExtractedRowsJson` stores the variable table structure as JSON so the database can handle variable column layouts.

## Running the pipeline

1) Web UI (single upload)

```powershell
python .\\src\\pdf_to_google_sheets_data_entry_pipeline\\main.py run_ui
# open http://127.0.0.1:5000
```

Upload a PDF using the web form — the server extracts the first table, writes to Google Sheets (if credentials present), saves an Excel fallback and a JSON report in `output/`.

2) Folder trigger (automated processing)

Create (or use defaults) the input and output folders. Then run:

```powershell
python .\\src\\pdf_to_google_sheets_data_entry_pipeline\\main.py run_with_trigger "MSI-WILLYPC\\SQLEXPRESS" PdfPipelineDB
```

Optional arguments:
- `<table_name>` — database table name (default `PdfExtractionRecords`)
- `<input_folder>` — path to watch for new PDFs (defaults to `input_pdfs/`)
- `<processed_folder>` — path where processed PDFs are moved (defaults to `processed_pdfs/`)
- `<excel_folder>` — path to save per-PDF Excel (defaults to `output/excel/`)
- `<report_folder>` — path to save per-PDF JSON report (defaults to `output/reports/`)
- `<poll_seconds>` — watcher interval in seconds (default 10)

Example with custom folders:

```powershell
python .\\src\\pdf_to_google_sheets_data_entry_pipeline\\main.py run_with_trigger "MSI-WILLYPC\\SQLEXPRESS" PdfPipelineDB PdfExtractionRecords "C:\\\\Input" "C:\\\\Processed" "C:\\\\ExcelOut" "C:\\\\Reports" 5
```

When a PDF is processed successfully the pipeline will:

1. Extract tables using `src/pdf_to_google_sheets_data_entry_pipeline/tools/pdf_table_extractor_tool.py`.
2. Save an Excel fallback using `src/pdf_to_google_sheets_data_entry_pipeline/google_sheets_helper.py::write_to_excel`.
3. Insert a record into the SQL Server table using `src/pdf_to_google_sheets_data_entry_pipeline/sql_server_helper.py` (the `ExtractedRowsJson` holds the table JSON).
4. Write a JSON report to the reports folder.
5. Move the original PDF to the processed folder (timestamped if name collision).

## Files and folders

- `src/pdf_to_google_sheets_data_entry_pipeline/main.py` — entrypoint and new `run_with_trigger` watcher
- `src/pdf_to_google_sheets_data_entry_pipeline/tools/pdf_table_extractor_tool.py` — PDF extraction logic (pdfplumber + optional OCR)
- `src/pdf_to_google_sheets_data_entry_pipeline/google_sheets_helper.py` — Google Sheets + Excel helpers
- `src/pdf_to_google_sheets_data_entry_pipeline/sql_server_helper.py` — SQL Server connection, table ensure, insert helper
- `src/pdf_to_google_sheets_data_entry_pipeline/web.py` — Flask upload UI
- `input_pdfs/` — default watched folder (created at runtime)
- `processed_pdfs/` — moved files after processing
- `output/excel/` — Excel fallbacks
- `output/reports/` — JSON processing reports

## Troubleshooting

- Missing `pyodbc`: install the package in your virtualenv: `python -m pip install pyodbc`.
- ODBC driver error: install Microsoft ODBC Driver 17/18 for SQL Server.
- If the watcher reports "OCR required" and no tables are found, install OCR tools: `pytesseract`, `pdf2image`, `Pillow`.
- If OCR fails with Poppler or Tesseract errors, make sure these env vars point to the right local binaries:
  - `POPPLER_PATH` should point to the folder containing Poppler DLLs, e.g. `C:\Users\HRIS\Downloads\EngineAI\poppler-26.02.0\Library\bin`
  - `TESSERACT_CMD` should point to `tesseract.exe`, e.g. `C:\Users\HRIS\AppData\Local\Programs\Tesseract-OCR\tesseract.exe`

Example PowerShell commands to set them for the current user:
```powershell
setx POPPLER_PATH "C:\Users\HRIS\Downloads\EngineAI\poppler-26.02.0\Library\bin"
setx TESSERACT_CMD "C:\Users\HRIS\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
```

Then restart your terminal and rerun the watcher.

## Security & best practices

- Never commit service account JSON or other secrets. Add them to `.gitignore`.
- Use Windows Authentication for the local SQL Server instance where possible (the helper currently uses Trusted Connection).
- Consider encrypting backups and restricting access to the service account and database.

## Development & Extensibility

- To add stricter structured storage, add a second normalized table and write additional code to map known column sets to normalized columns.
- To publish notifications (email/Slack), add a notifier module and call it on exceptions or on successful runs.

## License & Support

This project is provided as-is for your internal use. For questions about the crewAI integration or agent behavior see `src/pdf_to_google_sheets_data_entry_pipeline/config/agents.yaml` and `config/tasks.yaml`.

---

If you'd like I can also:

- remove generated `output/` and `processed_pdfs/` artifacts from the git history and re-push a cleaned branch (recommended), or
- add a short `HOWTO.md` with screenshots for using the web UI.

Tell me which you'd prefer and I'll update the repo accordingly.
