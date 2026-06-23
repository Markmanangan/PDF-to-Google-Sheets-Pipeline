import json
from datetime import datetime
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

    database_safe = database.replace("]", "]]" )
    with open_sql_server_connection(server, "master", autocommit=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"IF DB_ID(N'{database_safe}') IS NULL CREATE DATABASE [{database_safe}]"
        )


def ensure_table_exists(server: str, database: str, table_name: str) -> None:
    table_safe = table_name.replace("]", "]]" )
    create_sql = f"""
IF OBJECT_ID(N'dbo.[{table_safe}]', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.[{table_safe}]
    (
        Id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        DocumentName NVARCHAR(512) NOT NULL,
        SourceFilePath NVARCHAR(1024) NOT NULL,
        ExtractionTimestamp DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        ValidationStatus NVARCHAR(50) NOT NULL DEFAULT 'Pending',
        ValidationNotes NVARCHAR(MAX) NULL,
        ExtractedRowsJson NVARCHAR(MAX) NOT NULL,
        ExcelPath NVARCHAR(1024) NULL,
        ProcessedAt DATETIME2 NULL
    );
END
"""
    with open_sql_server_connection(server, database) as conn:
        cursor = conn.cursor()
        cursor.execute(create_sql)
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
    table_safe = table_name.replace("]", "]]" )
    insert_sql = f"""
INSERT INTO dbo.[{table_safe}] (
    DocumentName,
    SourceFilePath,
    ValidationStatus,
    ValidationNotes,
    ExtractedRowsJson,
    ExcelPath,
    ProcessedAt
)
OUTPUT INSERTED.Id
VALUES (?, ?, ?, ?, ?, ?, ?);
"""
    with open_sql_server_connection(server, database) as conn:
        cursor = conn.cursor()
        cursor.execute(
            insert_sql,
            (
                document_name,
                source_file_path,
                validation_status,
                validation_notes,
                extracted_json,
                excel_path,
                datetime.now(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
