#!/usr/bin/env python
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from crewai import LLM
from pdf_to_google_sheets_data_entry_pipeline.google_sheets_helper import write_to_excel
from pdf_to_google_sheets_data_entry_pipeline.crew import PdfToGoogleSheetsDataEntryPipelineCrew
from pdf_to_google_sheets_data_entry_pipeline.sql_server_helper import (
    ensure_database_exists,
    ensure_table_exists,
    insert_extraction_record,
)
from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool


def _patch_groq_cache_breakpoint_compat() -> None:
    """Groq rejects CrewAI's internal cache_breakpoint message field."""
    if getattr(LLM, "_groq_cache_breakpoint_patched", False):
        return

    original = LLM._format_messages_for_provider

    def _format_messages_for_provider(self, messages):
        from crewai.llms.cache import CACHE_BREAKPOINT_KEY

        cleaned = [
            {key: value for key, value in message.items() if key != CACHE_BREAKPOINT_KEY}
            for message in messages
        ]
        return original(self, cleaned)

    LLM._format_messages_for_provider = _format_messages_for_provider
    LLM._groq_cache_breakpoint_patched = True


def _save_result(result, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = None
    if hasattr(result, "json_dict"):
        data = result.json_dict
    elif hasattr(result, "pydantic") and result.pydantic is not None:
        data = result.pydantic.dict()
    else:
        raw = getattr(result, "raw", None)
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except ValueError:
                data = {"output": raw}
        else:
            data = raw

    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(data, output_file, indent=2, ensure_ascii=False)

    return output_path


def process_pdf_file(
    pdf_file_path: Path,
    excel_folder: Path,
    report_folder: Path,
    server: str,
    database: str,
    table_name: str,
) -> dict:
    extractor = PDFTableExtractorTool()
    extraction = extractor._run(str(pdf_file_path))
    if isinstance(extraction, str):
        try:
            extraction = json.loads(extraction)
        except ValueError:
            raise RuntimeError("Extractor returned invalid JSON")

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


def run(pdf_file_path: str | None = None, output_filename: str = "processing_report.json"):
    """
    Run the crew and save the final report to a local JSON file.
    """
    _patch_groq_cache_breakpoint_compat()
    if pdf_file_path is None:
        pdf_file_path = r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'

    inputs = {
        "pdf_file_path": pdf_file_path,
    }

    crew = PdfToGoogleSheetsDataEntryPipelineCrew().crew()
    result = crew.kickoff(inputs=inputs)

    output_dir = Path(os.getcwd()) / "output"
    output_path = _save_result(result, output_dir / output_filename)
    print(f"Saved crew result to {output_path}")
    return result


def run_with_trigger(
    server: str,
    database: str,
    table_name: str = "PdfExtractionRecords",
    input_folder: str | None = None,
    processed_folder: str | None = None,
    excel_folder: str | None = None,
    report_folder: str | None = None,
    poll_seconds: int = 10,
):
    _patch_groq_cache_breakpoint_compat()

    if input_folder is None:
        input_folder = str(Path.cwd() / "input_pdfs")
    if processed_folder is None:
        processed_folder = str(Path.cwd() / "processed_pdfs")
    if excel_folder is None:
        excel_folder = str(Path.cwd() / "output" / "excel")
    if report_folder is None:
        report_folder = str(Path.cwd() / "output" / "reports")

    input_path = Path(input_folder)
    processed_path = Path(processed_folder)
    excel_path = Path(excel_folder)
    report_path = Path(report_folder)

    input_path.mkdir(parents=True, exist_ok=True)
    processed_path.mkdir(parents=True, exist_ok=True)
    excel_path.mkdir(parents=True, exist_ok=True)
    report_path.mkdir(parents=True, exist_ok=True)

    ensure_database_exists(server, database)
    ensure_table_exists(server, database, table_name)

    print(f"Watching for PDFs in {input_path}")
    print(f"Processed files will move to {processed_path}")
    print(f"Excel output will save to {excel_path}")
    print(f"Reports will save to {report_path}")

    try:
        while True:
            pdf_files = sorted(input_path.glob("*.pdf"))
            if not pdf_files:
                time.sleep(poll_seconds)
                continue

            for pdf_file in pdf_files:
                print(f"Processing {pdf_file.name}...")
                try:
                    result = process_pdf_file(
                        pdf_file,
                        excel_path,
                        report_path,
                        server,
                        database,
                        table_name,
                    )
                    print(f"Success: {result['document_name']} -> {result['excel_path']}")

                    destination = processed_path / pdf_file.name
                    if destination.exists():
                        destination = processed_path / f"{pdf_file.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{pdf_file.suffix}"
                    shutil.move(str(pdf_file), str(destination))
                except Exception as exc:
                    print(f"Failed to process {pdf_file.name}: {exc}")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("Stopping trigger watcher.")


def train():
    """
    Train the crew for a given number of iterations.
    """
    inputs = {
        'pdf_file_path': r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'
    }
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().train(n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")

def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")

def test():
    """
    Test the crew execution and returns the results.
    """
    inputs = {
        'pdf_file_path': r'C:\Users\HRIS\Downloads\CrewAI_Friendly_BPI_Statement.pdf'
    }
    try:
        PdfToGoogleSheetsDataEntryPipelineCrew().crew().test(n_iterations=int(sys.argv[1]), openai_model_name=sys.argv[2], inputs=inputs)

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <command> [<args>]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "run":
        run()
    elif command == "train":
        train()
    elif command == "replay":
        replay()
    elif command == "test":
        test()
    elif command == "run_ui":
        from pdf_to_google_sheets_data_entry_pipeline.web import run_app

        run_app()
    elif command == "run_with_trigger":
        if len(sys.argv) < 4:
            print(
                "Usage: main.py run_with_trigger <server> <database> [<table_name>] [<input_folder>] [<processed_folder>] [<excel_folder>] [<report_folder>] [<poll_seconds>]"
            )
            sys.exit(1)

        server = sys.argv[2]
        database = sys.argv[3]
        table_name = sys.argv[4] if len(sys.argv) > 4 else "PdfExtractionRecords"
        input_folder = sys.argv[5] if len(sys.argv) > 5 else None
        processed_folder = sys.argv[6] if len(sys.argv) > 6 else None
        excel_folder = sys.argv[7] if len(sys.argv) > 7 else None
        report_folder = sys.argv[8] if len(sys.argv) > 8 else None
        poll_seconds = int(sys.argv[9]) if len(sys.argv) > 9 else 10

        run_with_trigger(
            server=server,
            database=database,
            table_name=table_name,
            input_folder=input_folder,
            processed_folder=processed_folder,
            excel_folder=excel_folder,
            report_folder=report_folder,
            poll_seconds=poll_seconds,
        )
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
