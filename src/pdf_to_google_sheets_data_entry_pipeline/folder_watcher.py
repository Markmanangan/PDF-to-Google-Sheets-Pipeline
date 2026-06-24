import os
import sys
import time
import shutil
from pathlib import Path
from datetime import datetime
import threading
import queue

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from pdf_to_google_sheets_data_entry_pipeline.main import process_pdf_file
from pdf_to_google_sheets_data_entry_pipeline.sql_server_helper import (
    ensure_database_exists,
    ensure_table_exists,
)


class PDFWatcherHandler(FileSystemEventHandler):
    def __init__(self, task_queue: queue.Queue):
        super().__init__()
        self.task_queue = task_queue

    def on_created(self, event):
        if not event.is_directory and event.src_path.lower().endswith(".pdf"):
            print(f"[Watcher] Detected new PDF: {Path(event.src_path).name}")
            self.task_queue.put(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.lower().endswith(".pdf"):
            print(f"[Watcher] Detected moved PDF: {Path(event.dest_path).name}")
            self.task_queue.put(Path(event.dest_path))


def process_queue_worker(
    task_queue: queue.Queue,
    in_folder: Path,
    completed_folder: Path,
    failed_folder: Path,
    excel_folder: Path,
    report_folder: Path,
    server: str,
    database: str,
    table_name: str,
):
    print("[Worker] Started background processing thread.")
    while True:
        pdf_path: Path = task_queue.get()
        if pdf_path is None:
            break  # Sentinel to stop thread

        print(f"\n[Worker] Starting extraction for: {pdf_path.name}")
        
        # Wait a moment for the file to finish copying before attempting to read it
        _wait_for_file_ready(pdf_path)

        try:
            result = process_pdf_file(
                pdf_file_path=pdf_path,
                excel_folder=excel_folder,
                report_folder=report_folder,
                server=server,
                database=database,
                table_name=table_name,
            )
            print(f"[Worker] Success! Updated sheet. Excel saved to: {result['excel_path']}")
            
            # Move to Completed with retry
            dest_path = completed_folder / pdf_path.name
            if dest_path.exists():
                dest_path = completed_folder / f"{pdf_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{pdf_path.suffix}"
            
            for _ in range(5):
                try:
                    shutil.move(str(pdf_path), str(dest_path))
                    break
                except PermissionError:
                    time.sleep(1)
            print(f"[Worker] Moved {pdf_path.name} to Completed folder.")
            
        except Exception as e:
            print(f"[Worker] ERROR processing {pdf_path.name}: {e}")
            
            # Move to Failed with retry
            dest_path = failed_folder / pdf_path.name
            if dest_path.exists():
                dest_path = failed_folder / f"{pdf_path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{pdf_path.suffix}"
            
            for _ in range(5):
                try:
                    shutil.move(str(pdf_path), str(dest_path))
                    break
                except PermissionError:
                    time.sleep(1)
            
            # Write error log
            error_log_path = dest_path.with_suffix(".txt")
            with open(error_log_path, "w", encoding="utf-8") as f:
                f.write(f"Error processing file at {datetime.now().isoformat()}:\n{str(e)}")
            
            print(f"[Worker] Moved {pdf_path.name} to Failed folder.")

        task_queue.task_done()


def _wait_for_file_ready(file_path: Path, max_retries: int = 20, delay: float = 0.5):
    """Wait until the file is no longer being written to by another process."""
    retries = 0
    while retries < max_retries:
        try:
            # Try renaming the file to itself. This fails if it's open by another process on Windows.
            if file_path.exists():
                os.rename(str(file_path), str(file_path))
                return True
        except OSError:
            pass
        time.sleep(delay)
        retries += 1
    return False


def run_folder_watcher(
    base_folder: str,
    server: str,
    database: str,
    table_name: str = "PdfExtractionRecords",
):
    base_path = Path(base_folder)
    in_folder = base_path / "In"
    completed_folder = base_path / "Completed"
    failed_folder = base_path / "Failed"
    excel_folder = base_path / "Excel"
    report_folder = base_path / "Reports"

    # Create directories if they don't exist
    for folder in [in_folder, completed_folder, failed_folder, excel_folder, report_folder]:
        folder.mkdir(parents=True, exist_ok=True)

    # Ensure DB tables exist
    ensure_database_exists(server, database)
    ensure_table_exists(server, database, table_name)

    print(f"===========================================================")
    print(f" AUTOMATED PDF WORKFLOW SERVER STARTED")
    print(f"===========================================================")
    print(f" Watching for PDFs in: {in_folder}")
    print(f" Database: {database} on {server}")
    print(f"===========================================================")

    task_queue = queue.Queue()

    # Start the worker thread
    worker_thread = threading.Thread(
        target=process_queue_worker,
        args=(task_queue, in_folder, completed_folder, failed_folder, excel_folder, report_folder, server, database, table_name),
        daemon=True
    )
    worker_thread.start()

    # CRASH PROOFING: Sweep the 'In' folder at startup for files dropped while offline
    existing_pdfs = sorted(in_folder.glob("**/*.pdf"))
    if existing_pdfs:
        print(f"[Startup] Found {len(existing_pdfs)} unprocessed PDFs from while we were offline. Queuing now...")
        for pdf in existing_pdfs:
            task_queue.put(pdf)

    # Setup the live Watchdog observer
    event_handler = PDFWatcherHandler(task_queue)
    observer = Observer()
    observer.schedule(event_handler, str(in_folder), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping folder watcher...")
        observer.stop()
        
    observer.join()


def cli_entry():
    if len(sys.argv) < 4:
        print("Usage: uv run folder_watcher <base_folder> <server> <database> [<table_name>]")
        print(r"Example: uv run folder_watcher C:\Shared_PDF_Folder MyServerName MyDatabaseName")
        sys.exit(1)

    base_folder = sys.argv[1]
    server = sys.argv[2]
    database = sys.argv[3]
    table_name = sys.argv[4] if len(sys.argv) > 4 else "PdfExtractionRecords"

    run_folder_watcher(base_folder, server, database, table_name)

if __name__ == "__main__":
    cli_entry()
