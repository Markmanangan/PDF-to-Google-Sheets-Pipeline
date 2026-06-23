from pathlib import Path
from time import time
from typing import List

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials


def write_to_google_sheet(
    creds_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    headers: List[str],
    rows: List[List],
    replace: bool = False,
) -> str:
    """
    Append rows to a Google Sheet using a service account JSON.
    If `replace` is True, the sheet will be cleared and headers written first.
    Returns the spreadsheet ID actually written to.
    """
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)

    sh = client.open_by_key(spreadsheet_id)

    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows=str(len(rows) + 10), cols=str(max(1, len(headers))))

    existing = worksheet.row_values(1)
    has_header = bool(existing and any(cell.strip() for cell in existing))

    if replace:
        worksheet.clear()
        worksheet.update("A1", [headers], value_input_option="RAW")
    elif not has_header:
        worksheet.update("A1", [headers], value_input_option="RAW")

    if rows:
        worksheet.append_rows(rows, value_input_option="RAW")

    return spreadsheet_id


def write_to_excel(output_path: str, headers: List[str], rows: List[List]):
    df = pd.DataFrame(rows, columns=headers)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if output_file.exists():
        try:
            output_file.unlink()
        except PermissionError:
            timestamp = int(time())
            output_file = output_file.with_name(f"{output_file.stem}_{timestamp}{output_file.suffix}")

    df.to_excel(output_file, index=False)
    return str(output_file)
