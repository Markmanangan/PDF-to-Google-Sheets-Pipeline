import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyodbc


def _get_odbc_driver() -> str:
    installed_drivers = [driver for driver in pyodbc.drivers()]
    for driver in [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]:
        if driver in installed_drivers:
            return driver
    raise RuntimeError(
        "No supported SQL Server ODBC driver found. "
        "Install ODBC Driver 17 or 18 for SQL Server."
    )


def open_sql_server_connection(server: str, database: str = "master", autocommit: bool = False) -> pyodbc.Connection:
    driver = _get_odbc_driver()
    connection_string = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(connection_string, autocommit=autocommit)


def ensure_database_exists(server: str, database: str) -> None:
    if database.lower() == "master":
        return

    # Call the stored procedure in the master database
    call_sql = "EXEC sp_EnsureDatabaseExists @DatabaseName = ?;"
    with open_sql_server_connection(server, "master", autocommit=True) as conn:
        cursor = conn.cursor()
        cursor.execute(call_sql, database)


def ensure_table_exists(server: str, database: str, table_name: str) -> None:
    # Call the stored procedure instead of raw CREATE TABLE
    call_sql = "EXEC sp_EnsureTableExists @TableName = ?;"
    with open_sql_server_connection(server, database) as conn:
        cursor = conn.cursor()
        cursor.execute(call_sql, table_name)
        conn.commit()


def insert_extraction_record(
    server: str,
    database: str,
    table_name: str,
    document_name: str,
    source_file_path: str,
    extracted_rows: Any,
    excel_path: str | None = None,
    validation_status: str = "Success",
    validation_notes: str | None = None,
) -> int:
    extracted_json = json.dumps(extracted_rows, ensure_ascii=False)
    processed_at = datetime.now(timezone.utc)
    
    # We call the stored procedure instead of raw INSERT
    call_sql = """
    EXEC sp_InsertExtractionRecord 
        @TableName = ?, 
        @DocumentName = ?, 
        @SourceFilePath = ?, 
        @ValidationStatus = ?, 
        @ValidationNotes = ?, 
        @ExtractedRowsJson = ?, 
        @ExcelPath = ?, 
        @ProcessedAt = ?;
    """
    with open_sql_server_connection(server, database) as conn:
        cursor = conn.cursor()
        cursor.execute(
            call_sql,
            table_name,
            document_name,
            source_file_path,
            validation_status,
            validation_notes,
            extracted_json,
            excel_path,
            processed_at,
        )
        row = cursor.fetchone()
        conn.commit()
        if row:
            return int(row[0])
        return -1
