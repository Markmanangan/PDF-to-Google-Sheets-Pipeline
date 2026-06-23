import json
import os
from pathlib import Path
from flask import Flask, redirect, render_template_string, request, send_file, url_for

from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool
from pdf_to_google_sheets_data_entry_pipeline.google_sheets_helper import write_to_google_sheet, write_to_excel

app = Flask(__name__)

# Try to auto-detect the service account JSON you placed at the project root
_PROJECT_ROOT = Path.cwd()
_DEFAULT_SERVICE_ACCOUNT = _PROJECT_ROOT / "pdfengine-4bc5c52f4cb5.json"
# Default spreadsheet id taken from the working shared spreadsheet URL
_DEFAULT_SPREADSHEET_ID = "1bLfjBJBEusQz7WDL_hJkDlJs5HOkbDywgo6doeOJ7v8"
_DEFAULT_SHEET_NAME = "Sheet1"

TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>PDF to Google Sheets Pipeline</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 40px; }
      label { display: block; margin: 12px 0 4px; }
      input[type=file] { width: 100%; padding: 8px; }
      button { margin-top: 12px; padding: 10px 16px; }
      .result { margin-top: 24px; padding: 16px; border: 1px solid #ddd; background: #f9f9f9; }
      .hint { color: #555; font-size: 0.95em; }
    </style>
  </head>
  <body>
    <h1>PDF to Google Sheets Pipeline</h1>
    <p class="hint">Upload a PDF file. The server will extract the first table and write it to Google Sheets and save an Excel fallback.</p>

    <form method="post" enctype="multipart/form-data">
      <label for="pdf_file">Upload PDF</label>
      <input id="pdf_file" name="pdf_file" type="file" accept="application/pdf" required />
      <button type="submit">Run Pipeline</button>
    </form>

    {% if result %}
    <div class="result">
      <h2>Result</h2>
      <pre>{{ result }}</pre>
      {% if excel_url %}<p>Excel: <a href="{{ excel_url }}">Download</a></p>{% endif %}
      {% if json_url %}<p>Report: <a href="{{ json_url }}">Download</a></p>{% endif %}
    </div>
    {% endif %}
  </body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    excel_url = None
    json_url = None

    if request.method == "POST":
        file = request.files.get("pdf_file")
        if not file:
            result = "No file uploaded"
            return render_template_string(TEMPLATE, result=result)

        uploads = Path.cwd() / "output" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        saved_path = uploads / file.filename
        file.save(saved_path)

        try:
            extractor = PDFTableExtractorTool()
            extraction = extractor._run(str(saved_path))
            if isinstance(extraction, str):
                try:
                    extraction = json.loads(extraction)
                except Exception:
                    raise RuntimeError("Extractor returned invalid JSON")

            if extraction.get("error"):
                raise RuntimeError(extraction["error"])

            tables = extraction.get("tables", [])
            if not tables:
                raise RuntimeError("No tables found in PDF")
            first = tables[0]
            headers = first.get("headers", [])
            rows = first.get("rows", [])

            # Determine creds and spreadsheet
            service_account = _DEFAULT_SERVICE_ACCOUNT if _DEFAULT_SERVICE_ACCOUNT.exists() else None
            if service_account is None:
                raise RuntimeError("Service account JSON not found in project root. Please add it or use a different method.")

            spreadsheet_id = _DEFAULT_SPREADSHEET_ID
            sheet_name = _DEFAULT_SHEET_NAME

            # Append rows to Google Sheets
            actual_spreadsheet_id = write_to_google_sheet(
                str(service_account), spreadsheet_id, sheet_name, headers, rows, replace=False
            )

            # Excel fallback
            stem = Path(file.filename).stem
            excel_path = Path.cwd() / "output" / f"{stem}.xlsx"
            write_to_excel(str(excel_path), headers, rows)

            # JSON report
            report = {
                "document_name": str(saved_path),
                "status": "success",
                "records_extracted": len(rows),
                "rows_inserted": len(rows) + 1,
                "spreadsheet_id": actual_spreadsheet_id,
                "errors": [],
            }
            json_path = Path.cwd() / "output" / f"{stem}.json"
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

            result = (
                f"Wrote {len(rows)} rows to sheet {actual_spreadsheet_id} / {sheet_name} "
                "and saved files."
            )
            excel_url = url_for("download_file_path", filepath=str(excel_path.relative_to(Path.cwd())))
            json_url = url_for("download_file_path", filepath=str(json_path.relative_to(Path.cwd())))

        except Exception as exc:
            result = f"Error: {exc}"

    return render_template_string(TEMPLATE, result=result, excel_url=excel_url, json_url=json_url)


@app.route("/download/<path:filename>")
def download_file(filename):
    path = Path.cwd() / "output" / filename
    if not path.exists():
        return "File not found", 404
    return send_file(path, as_attachment=True)


@app.route("/download_path/<path:filepath>")
def download_file_path(filepath):
    path = Path.cwd() / filepath
    if not path.exists():
        return "File not found", 404
    return send_file(path, as_attachment=True)


def run_app(host: str = "127.0.0.1", port: int = 5000):
    app.run(host=host, port=port, debug=True)
