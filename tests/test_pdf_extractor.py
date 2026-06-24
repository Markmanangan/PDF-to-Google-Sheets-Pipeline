import json
import os
from pathlib import Path

from pdf_to_google_sheets_data_entry_pipeline.tools.pdf_table_extractor_tool import PDFTableExtractorTool
from pdf_to_google_sheets_data_entry_pipeline.google_sheets_helper import write_to_excel


def test_sample_bank_statement_extraction(tmp_path):
    os.environ["POPPLER_PATH"] = r"C:\Users\HRIS\Downloads\EngineAI\poppler-26.02.0\Library\bin"
    os.environ["TESSERACT_CMD"] = r"C:\Users\HRIS\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
    sample_pdf = Path("processed_pdfs/Sample_Bank_Statement.pdf")
    assert sample_pdf.exists(), f"Sample PDF not found at {sample_pdf.resolve()}"

    extractor = PDFTableExtractorTool()
    result_json = extractor._run(str(sample_pdf))
    data = json.loads(result_json)

    assert data["classification"] == "OCR_REQUIRED"
    assert data["errors"] == []
    assert data["tables"], "Expected extracted table rows from sample PDF"

    table = data["tables"][0]
    assert table["headers"] == ["Date", "Description", "Ref", "Debit Amt", "Credit Amt", "Balance"]
    assert len(table["rows"]) == 4
    assert table["rows"][0][0] == "JAN 05"
    assert table["rows"][0][4] == "25000.00"
    assert table["rows"][0][5] == "25000.00"

    excel_path = tmp_path / "Sample_Bank_Statement.xlsx"
    written_path = write_to_excel(str(excel_path), table["headers"], table["rows"])
    assert Path(written_path).exists()

def test_mark_pdf_extraction(tmp_path):
    os.environ["POPPLER_PATH"] = r"C:\Users\HRIS\Downloads\EngineAI\poppler-26.02.0\Library\bin"
    os.environ["TESSERACT_CMD"] = r"C:\Users\HRIS\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
    mark_pdf = Path("processed_pdfs/Mark.pdf")
    assert mark_pdf.exists(), f"Mark PDF not found at {mark_pdf.resolve()}"

    extractor = PDFTableExtractorTool()
    result_json = extractor._run(str(mark_pdf))
    data = json.loads(result_json)

    assert data["classification"] == "OCR_REQUIRED"
    assert data["errors"] == []
    assert data["tables"], "Expected extracted table rows from Mark PDF"

    table = data["tables"][0]
    assert table["headers"] == ["Date", "Description", "Ref", "Details", "Debit Amt", "Credit Amt", "Balance"]
    assert len(table["rows"]) == 12, f"Expected 12 rows (including PREVIOUS BALANCE), got {len(table['rows'])}"

    # Row 0: PREVIOUS BALANCE
    assert table["rows"][0][1] == "PREVIOUS BALANCE"
    assert table["rows"][0][6] == "150,488.63"

    # Row 1: DEC 10
    assert table["rows"][1][0] == "DEC 10"
    assert table["rows"][1][3] == "INTER-BANK FUND TRANSFER"
    assert table["rows"][1][4] == "" # Debit
    assert table["rows"][1][5] == "20,035.51" # Credit
    assert table["rows"][1][6] == "170,524.14" # Balance
    
    # Row 2: DEC 15
    assert table["rows"][2][0] == "DEC 15"
    assert table["rows"][2][3] == "FROM:NON-BPI TERMINAL"
    assert table["rows"][2][4] == "10,000.00" # Debit
    assert table["rows"][2][5] == "" # Credit
    assert table["rows"][2][6] == "160,524.14" # Balance

    excel_path = tmp_path / "Mark.xlsx"
    written_path = write_to_excel(str(excel_path), table["headers"], table["rows"])
    assert Path(written_path).exists()
