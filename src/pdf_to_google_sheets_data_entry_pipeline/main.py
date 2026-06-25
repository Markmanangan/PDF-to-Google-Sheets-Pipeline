#!/usr/bin/env python
"""PDF → structured data pipeline entry point.

Single-PDF processing is exposed via :func:`process_pdf_file`, which both the
Flask web UI (``web.py``) and the watchdog-based folder watcher
(``folder_watcher.py``) call. Continuous automation is handled entirely by
``folder_watcher.py`` -- the polling watcher that previously lived here has
been removed in favour of that single canonical implementation.

CLI usage:
    python main.py process <pdf_path> [<server> <database>] [<table_name>]
    python main.py run_ui
"""
import json
import sys
from pathlib import Path

from pdf_to_google_sheets_data_entry_pipeline.google_sheets_helper import write_to_excel
from pdf_to_google_sheets_data_entry_pipeline.sql_server_helper import (
    insert_extraction_record,
)
from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool


def _parse_extraction(extraction) -> dict:
    """Accept either a JSON string or a dict and return a normalised dict."""
    if isinstance(extraction, str):
        try:
            extraction = json.loads(extraction)
        except ValueError:
            raise RuntimeError("Extractor returned invalid JSON")
    return extraction


def process_pdf_file(
    pdf_file_path: Path,
    excel_folder: Path,
    report_folder: Path,
    server: str,
    database: str,
    table_name: str,
) -> dict:
    """Extract the first table from a PDF and write it to Excel + SQL Server.

    Returns a small report dict. Raises ``RuntimeError`` on any failure so the
    caller (e.g. ``folder_watcher.py``) can route the file to its Failed folder.
    """
    extractor = PDFTableExtractorTool()
    extraction = _parse_extraction(extractor._run(str(pdf_file_path)))

    if extraction.get("error"):
        raise RuntimeError(extraction["error"])

    errors = extraction.get("errors") or []
    if extraction.get("classification") == "ERROR" or errors:
        raise RuntimeError("; ".join(errors))

    tables = extraction.get("tables", [])
    if not tables:
        raise RuntimeError("No tables found in PDF")

    first_table = tables[0]
    headers = first_table.get("headers", [])
    rows = first_table.get("rows", [])

    if not headers:
        raise RuntimeError("Extracted table did not contain headers")

    excel_folder.mkdir(parents=True, exist_ok=True)
    report_folder.mkdir(parents=True, exist_ok=True)

    stem = pdf_file_path.stem
    excel_path = excel_folder / f"{stem}.xlsx"
    actual_excel_path = write_to_excel(str(excel_path), headers, rows)

    record_id = insert_extraction_record(
        server=server,
        database=database,
        table_name=table_name,
        document_name=pdf_file_path.name,
        source_file_path=str(pdf_file_path),
        extracted_rows=first_table,
        excel_path=str(actual_excel_path),
        validation_status="Success",
        validation_notes=None,
    )

    report = {
        "document_name": str(pdf_file_path),
        "status": "success",
        "records_extracted": len(rows),
        "rows_inserted": len(rows),
        "excel_path": str(excel_path),
        "database_record_id": record_id,
        "errors": [],
    }

    report_path = report_folder / f"{stem}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def process(pdf_file_path: str, server: str, database: str, table_name: str = "PdfExtractionRecords") -> dict:
    """Process a single PDF file and save outputs under ``./output``.

    Convenience wrapper around :func:`process_pdf_file` for ad-hoc one-off runs
    that don't need the full folder-watcher setup.
    """
    output_dir = Path.cwd() / "output"
    return process_pdf_file(
        pdf_file_path=Path(pdf_file_path),
        excel_folder=output_dir / "excel",
        report_folder=output_dir / "reports",
        server=server,
        database=database,
        table_name=table_name,
    )


def cli():
    """Console-script entry point that dispatches subcommands.

    Usage:
        pdf_to_google_sheets_data_entry_pipeline run_ui
        pdf_to_google_sheets_data_entry_pipeline process <pdf_path> <server> <database> [<table_name>]
    """
    if len(sys.argv) < 2:
        print("Usage: pdf_to_google_sheets_data_entry_pipeline <command> [<args>]")
        print("Commands:")
        print("  run_ui                                                Start the Flask upload UI")
        print("  process <pdf_path> <server> <database> [<table_name>] Process one PDF")
        sys.exit(1)

    command = sys.argv[1]
    if command == "run_ui":
        from pdf_to_google_sheets_data_entry_pipeline.web import run_app

        run_app()

    elif command == "process":
        if len(sys.argv) < 5:
            print("Usage: pdf_to_google_sheets_data_entry_pipeline process <pdf_path> <server> <database> [<table_name>]")
            sys.exit(1)
        pdf_file_path = sys.argv[2]
        server = sys.argv[3]
        database = sys.argv[4]
        table_name = sys.argv[5] if len(sys.argv) > 5 else "PdfExtractionRecords"

        result = process(pdf_file_path, server, database, table_name)
        print(f"Success: {result['document_name']} -> {result['excel_path']}")
        print(f"  Records extracted: {result['records_extracted']}")
        print(f"  Database record id: {result['database_record_id']}")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
