import json
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, send_file, url_for

from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool
from pdf_to_google_sheets_data_entry_pipeline.google_sheets_helper import write_to_google_sheet, write_to_excel

app = Flask(__name__)

_PROJECT_ROOT = Path.cwd()
_DEFAULT_SERVICE_ACCOUNT = _PROJECT_ROOT / "pdfengine-4bc5c52f4cb5.json"
_DEFAULT_SPREADSHEET_ID = "1bLfjBJBEusQz7WDL_hJkDlJs5HOkbDywgo6doeOJ7v8"
_DEFAULT_SHEET_NAME = "Sheet1"

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EngineAI &mdash; PDF Extractor</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --blue:    #2563eb;
    --blue-bg: #eff6ff;
    --green:   #16a34a;
    --green-bg:#f0fdf4;
    --red:     #dc2626;
    --red-bg:  #fef2f2;
    --gray:    #6b7280;
    --gray-bg: #f9fafb;
    --border:  #e5e7eb;
    --shadow:  0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.06);
    --radius:  12px;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--gray-bg);
    color: #1f2937;
    line-height: 1.6;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ---------- header ---------- */
  .header {
    background: #fff;
    border-bottom: 1px solid var(--border);
    padding: 24px 0;
    text-align: center;
  }
  .header h1 {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--blue);
    letter-spacing: -.02em;
  }
  .header p {
    margin-top: 6px;
    color: var(--gray);
    font-size: .95rem;
  }

  /* ---------- main ---------- */
  .main {
    flex: 1;
    width: 100%;
    max-width: 720px;
    margin: 40px auto;
    padding: 0 20px;
  }

  /* ---------- upload zone ---------- */
  .drop-zone {
    position: relative;
    border: 2px dashed var(--border);
    border-radius: var(--radius);
    background: #fff;
    padding: 48px 24px;
    text-align: center;
    cursor: pointer;
    transition: border-color .2s, background .2s;
  }
  .drop-zone:hover,
  .drop-zone.dragover {
    border-color: var(--blue);
    background: var(--blue-bg);
  }
  .drop-zone .icon {
    font-size: 2.4rem;
    margin-bottom: 12px;
  }
  .drop-zone .label {
    font-size: 1rem;
    color: var(--gray);
  }
  .drop-zone .label strong {
    color: var(--blue);
  }
  .drop-zone input[type=file] {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
  }
  .file-chosen {
    margin-top: 14px;
    font-weight: 600;
    color: #111;
    word-break: break-all;
  }

  /* ---------- button ---------- */
  .btn {
    display: block;
    width: 100%;
    margin-top: 20px;
    padding: 14px;
    font-size: 1rem;
    font-weight: 600;
    color: #fff;
    background: var(--blue);
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
    transition: opacity .2s;
  }
  .btn:hover { opacity: .9; }
  .btn:disabled {
    opacity: .5;
    cursor: not-allowed;
  }

  /* spinner */
  .spinner {
    display: inline-block;
    width: 18px;
    height: 18px;
    border: 3px solid rgba(255,255,255,.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin .6s linear infinite;
    vertical-align: middle;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ---------- status cards ---------- */
  .card {
    margin-top: 24px;
    padding: 20px 24px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: #fff;
    box-shadow: var(--shadow);
  }
  .card.success { border-left: 4px solid var(--green); background: var(--green-bg); }
  .card.error   { border-left: 4px solid var(--red);   background: var(--red-bg); }
  .card h2 {
    font-size: 1.05rem;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card.success h2 { color: var(--green); }
  .card.error   h2 { color: var(--red); }
  .card p, .card ul { font-size: .95rem; color: #374151; }
  .card ul { padding-left: 20px; margin-top: 6px; }
  .card ul li { margin-bottom: 4px; }

  .downloads {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
  }
  .downloads a {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 10px 18px;
    font-size: .9rem;
    font-weight: 600;
    color: var(--blue);
    background: #fff;
    border: 1px solid var(--blue);
    border-radius: 8px;
    text-decoration: none;
    transition: background .2s, color .2s;
  }
  .downloads a:hover {
    background: var(--blue);
    color: #fff;
  }

  /* ---------- data preview ---------- */
  .preview {
    margin-top: 20px;
    overflow-x: auto;
  }
  .preview h3 {
    font-size: .95rem;
    color: var(--gray);
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: .04em;
  }
  .preview table {
    width: 100%;
    border-collapse: collapse;
    font-size: .9rem;
  }
  .preview th {
    background: var(--blue);
    color: #fff;
    font-weight: 600;
    text-align: left;
    padding: 10px 14px;
    white-space: nowrap;
  }
  .preview td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .preview tr:nth-child(even) td { background: var(--gray-bg); }
  .preview tr:hover td { background: var(--blue-bg); }

  /* ---------- footer ---------- */
  .footer {
    text-align: center;
    padding: 20px;
    color: var(--gray);
    font-size: .85rem;
  }

  /* responsive */
  @media (max-width: 600px) {
    .main { margin: 24px auto; padding: 0 12px; }
    .drop-zone { padding: 32px 16px; }
    .downloads { flex-direction: column; }
  }
</style>
</head>
<body>

  <!-- header -->
  <div class="header">
    <h1>&#9889; EngineAI &mdash; PDF Extractor</h1>
    <p>Upload a PDF bank statement and extract its table data automatically.</p>
  </div>

  <!-- main -->
  <div class="main">

    <!-- upload form -->
    <form id="upload-form" method="post" enctype="multipart/form-data">
      <div class="drop-zone" id="drop-zone">
        <div class="icon">&#128196;</div>
        <div class="label"><strong>Click to browse</strong> or drag a PDF here</div>
        <div class="file-chosen" id="file-name"></div>
        <input type="file" name="pdf_file" id="pdf-file" accept=".pdf,application/pdf" required>
      </div>
      <button type="submit" class="btn" id="submit-btn">Extract Data</button>
    </form>

    <!-- result card -->
    {% if result %}
    <div class="card {{ status }}">
      <h2>{{ icon|safe }} {{ title }}</h2>
      <p>{{ message }}</p>
      {% if details %}
      <ul>
        {% for d in details %}<li>{{ d }}</li>{% endfor %}
      </ul>
      {% endif %}
      {% if excel_url or json_url %}
      <div class="downloads">
        {% if excel_url %}<a href="{{ excel_url }}">&#128196; Download Excel</a>{% endif %}
        {% if json_url  %}<a href="{{ json_url }}">&#128193; Download Report</a>{% endif %}
      </div>
      {% endif %}
    </div>
    {% endif %}

    <!-- data preview table -->
    {% if table_headers %}
    <div class="preview">
      <h3>Extracted Data Preview</h3>
      <table>
        <thead><tr>
          {% for h in table_headers %}<th>{{ h }}</th>{% endfor %}
        </tr></thead>
        <tbody>
          {% for row in table_rows %}
          <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

  </div>

  <!-- footer -->
  <div class="footer">
    {% if doc_name %}Processed: {{ doc_name }}{% else %}EngineAI &copy; 2026{% endif %}
  </div>

  <!-- minimal JS: drag-drop + submit spinner -->
  <script>
  (function() {
    var form = document.getElementById('upload-form');
    var dropZone = document.getElementById('drop-zone');
    var fileInput = document.getElementById('pdf-file');
    var fileName = document.getElementById('file-name');
    var btn = document.getElementById('submit-btn');

    fileInput.addEventListener('change', function() {
      fileName.textContent = this.files.length ? this.files[0].name : '';
    });

    ['dragenter','dragover'].forEach(function(ev) {
      dropZone.addEventListener(ev, function(e) {
        e.preventDefault();
        dropZone.classList.add('dragover');
      });
    });
    ['dragleave','drop'].forEach(function(ev) {
      dropZone.addEventListener(ev, function(e) {
        e.preventDefault();
        dropZone.classList.remove('dragover');
      });
    });
    dropZone.addEventListener('drop', function(e) {
      fileInput.files = e.dataTransfer.files;
      fileName.textContent = e.dataTransfer.files.length ? e.dataTransfer.files[0].name : '';
    });

    form.addEventListener('submit', function() {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Processing\u2026';
    });
  })();
  </script>

</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    status = None
    icon = None
    title = None
    message = None
    details = None
    excel_url = None
    json_url = None
    doc_name = None
    table_headers = None
    table_rows = None

    if request.method == "POST":
        file = request.files.get("pdf_file")
        if not file or not file.filename:
            status, icon, title, message = "error", "&#10060;", "No file selected", "Please choose a PDF file to upload."
            return render_template_string(
                TEMPLATE, result=True, status=status, icon=icon, title=title,
                message=message, details=details, excel_url=excel_url,
                json_url=json_url, doc_name=doc_name,
                table_headers=table_headers, table_rows=table_rows,
            )

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
            # --- Google Sheets writing disabled for local-only deployment ---
            # Re-enable by uncommenting the block below and ensuring the service
            # account JSON + spreadsheet ID are configured.
            # service_account = _DEFAULT_SERVICE_ACCOUNT if _DEFAULT_SERVICE_ACCOUNT.exists() else None
            # if service_account is None:
            #     raise RuntimeError(
            #         "Google service-account JSON not found in project root. "
            #         "Place it at the project root or configure the path."
            #     )
            # spreadsheet_id = _DEFAULT_SPREADSHEET_ID
            # sheet_name = _DEFAULT_SHEET_NAME
            # actual_spreadsheet_id = write_to_google_sheet(
            #     str(service_account), spreadsheet_id, sheet_name, headers, rows, replace=False
            # )
            actual_spreadsheet_id = None

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

            status = "success"
            icon = "&#9989;"
            title = "Extraction Complete"
            message = f"{len(rows)} rows extracted successfully."
            details = [
                f"Excel saved: {stem}.xlsx",
                f"Report saved: {stem}.json",
            ]
            doc_name = file.filename
            table_headers = headers
            table_rows = rows

            excel_url = url_for("download_file_path", filepath=str(excel_path.relative_to(Path.cwd())))
            json_url = url_for("download_file_path", filepath=str(json_path.relative_to(Path.cwd())))

        except Exception as exc:
            status = "error"
            icon = "&#10060;"
            title = "Extraction Failed"
            message = str(exc)
            doc_name = file.filename

    return render_template_string(
        TEMPLATE, result=bool(result or status), status=status, icon=icon,
        title=title, message=message, details=details, excel_url=excel_url,
        json_url=json_url, doc_name=doc_name,
        table_headers=table_headers, table_rows=table_rows,
    )


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
