import json
import re
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

class PDFTableExtractorInput(BaseModel):
    """Input schema for PDFTableExtractorTool."""

    pdf_file_path: str = Field(
        ...,
        description="Path to the PDF bank statement file to extract transactions from.",
    )

class PDFTableExtractorTool(BaseTool):
    """Tool for extracting bank statement transactions from a PDF file.

    Uses pdfplumber for text extraction, then applies regex parsing and
    balance-delta math to correctly assign Debit Amt vs Credit Amt columns.
    """

    name: str = "PDFTableExtractorTool"
    description: str = (
        "Extracts bank statement transaction tables from a PDF file. "
        "Uses balance comparison in Python to correctly assign Debit Amt and Credit Amt columns. "
        "Returns structured JSON with headers and rows, empty cells preserved."
    )
    args_schema: Type[BaseModel] = PDFTableExtractorInput

    def _run(self, pdf_file_path: str) -> str:
        """
        Opens the PDF at pdf_file_path, attempts native text extraction first,
        then falls back to OCR if the text layer is missing or below threshold.

        Returns structured JSON containing:
          - classification: NATIVE_TEXT | OCR_REQUIRED
          - pdf_text: extracted text (or OCR text)
          - tables: extracted tables when found
          - errors: optional errors list
        """
        # ---------------------------------------------------------------------------
        # 1. Lazy import so missing dependency gives a friendly error at runtime
        # ---------------------------------------------------------------------------
        try:
            import pdfplumber
        except ImportError:
            return json.dumps({
                "tables": [],
                "classification": "ERROR",
                "errors": [
                    "pdfplumber is not installed. Please install it with: pip install pdfplumber"
                ],
            })

        ocr_available = True
        try:
            import pytesseract  # type: ignore[attr-defined]
            from PIL import Image  # type: ignore[attr-defined]
            from pdf2image import convert_from_path  # type: ignore[attr-defined]
        except ImportError:
            ocr_available = False

        # ---------------------------------------------------------------------------
        # 2. Open PDF and extract native text
        # ---------------------------------------------------------------------------
        try:
            with pdfplumber.open(pdf_file_path) as pdf:
                pages = list(pdf.pages)
                native_text_pages: list[str] = []
                native_word_count = 0
                for page in pages:
                    text = page.extract_text(x_tolerance=1, y_tolerance=1) or ""
                    native_text_pages.append(text)
                    native_word_count += len(text.split())

                native_text = "\n\n".join(native_text_pages).strip()
                native_text_length = len(native_text)
                page_has_text = any(page.extract_text() for page in pages)
                page_word_count = sum(len((page.extract_text() or "").split()) for page in pages)

                classification = "NATIVE_TEXT"
                if not page_has_text or page_word_count < 12:
                    classification = "OCR_REQUIRED"

                if classification == "NATIVE_TEXT":
                    extracted_pages = pages
                else:
                    extracted_pages = pages

                def normalize_cell(value: str) -> str:
                    return value.strip() if isinstance(value, str) else ""

                def normalize_amount(value: str) -> str:
                    if not isinstance(value, str):
                        return ""
                    return value.replace(",", "").strip()

                tables: list[dict] = []
                for page in extracted_pages:
                    for table in page.extract_tables():
                        if not table or len(table) < 2:
                            continue
                        raw_header = [normalize_cell(cell).lower() for cell in table[0]]
                        if not raw_header:
                            continue
                        expected_keys = ["date", "description", "ref", "details", "debit", "credit", "balance"]
                        if all(any(key in cell for cell in raw_header) for key in expected_keys):
                            index_map = {
                                "date": next(i for i, c in enumerate(raw_header) if "date" in c),
                                "description": next(i for i, c in enumerate(raw_header) if "description" in c),
                                "ref": next(i for i, c in enumerate(raw_header) if "ref" in c),
                                "details": next(i for i, c in enumerate(raw_header) if "detail" in c),
                                "debit": next(i for i, c in enumerate(raw_header) if "debit" in c),
                                "credit": next(i for i, c in enumerate(raw_header) if "credit" in c),
                                "balance": next(i for i, c in enumerate(raw_header) if "balance" in c),
                            }
                            rows: list[list[str]] = []
                            for row in table[1:]:
                                if not row or all(not normalize_cell(cell) for cell in row):
                                    continue
                                rows.append([
                                    normalize_cell(row[index_map["date"]]) if index_map["date"] < len(row) else "",
                                    normalize_cell(row[index_map["description"]]) if index_map["description"] < len(row) else "",
                                    normalize_cell(row[index_map["ref"]]) if index_map["ref"] < len(row) else "",
                                    normalize_cell(row[index_map["details"]]) if index_map["details"] < len(row) else "",
                                    normalize_amount(row[index_map["debit"]]) if index_map["debit"] < len(row) else "",
                                    normalize_amount(row[index_map["credit"]]) if index_map["credit"] < len(row) else "",
                                    normalize_amount(row[index_map["balance"]]) if index_map["balance"] < len(row) else "",
                                ])
                            if rows:
                                tables.append({"headers": ["Date", "Description", "Ref", "Details", "Debit Amt", "Credit Amt", "Balance"], "rows": rows})

                if tables:
                    return json.dumps(
                        {
                            "classification": classification,
                            "pdf_text": native_text,
                            "tables": tables,
                            "errors": [],
                        },
                        ensure_ascii=False,
                    )

                if classification == "OCR_REQUIRED":
                    if not ocr_available:
                        return json.dumps({
                            "classification": classification,
                            "pdf_text": native_text,
                            "tables": [],
                            "errors": [
                                "OCR is required but pytesseract/pdf2image/Pillow are not installed. "
                                "Install them with: pip install pytesseract pdf2image Pillow"
                            ],
                        })

                    try:
                        images = convert_from_path(pdf_file_path, dpi=300)
                        ocr_text_pages = [pytesseract.image_to_string(image) for image in images]
                        ocr_text = "\n\n".join(ocr_text_pages).strip()
                        ocr_lines = [line for page_text in ocr_text_pages for line in page_text.split("\n") if line.strip()]
                    except Exception as exc:
                        return json.dumps({
                            "classification": "OCR_FAILED",
                            "pdf_text": native_text,
                            "tables": [],
                            "errors": [
                                f"OCR conversion failed: {exc}"
                            ],
                        })

                    ocr_rows: list[list[str]] = []
                    header_index = None
                    for i, line in enumerate(ocr_lines):
                        if "Debit" in line and "Credit" in line and "Balance" in line:
                            header_index = i
                            break
                    if header_index is not None:
                        number_pattern = re.compile(r"[\d,]+\.\d{2}")
                        date_pattern = re.compile(
                            r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d{1,2}",
                            re.IGNORECASE,
                        )
                        prev_balance: float | None = None
                        for line in ocr_lines[header_index + 1:]:
                            line = line.strip()
                            if not line or not date_pattern.match(line):
                                continue
                            numbers = number_pattern.findall(line)
                            if len(numbers) < 2:
                                continue
                            balance_str = numbers[-1].replace(",", "")
                            amount_str = numbers[-2].replace(",", "")
                            try:
                                balance = float(balance_str)
                            except ValueError:
                                continue
                            text_part = number_pattern.sub("", line).strip()
                            parts = text_part.split()
                            date = " ".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")
                            rest = parts[2:] if len(parts) > 2 else []
                            description = rest[0] if len(rest) > 0 else ""
                            ref = rest[1] if len(rest) > 1 else ""
                            details = " ".join(rest[2:]) if len(rest) > 2 else ""
                            if prev_balance is None or balance < prev_balance - 0.001:
                                debit_amt = amount_str
                                credit_amt = ""
                            else:
                                debit_amt = ""
                                credit_amt = amount_str
                            prev_balance = balance
                            ocr_rows.append([date, description, ref, details, debit_amt, credit_amt, balance_str])

                    if ocr_rows:
                        return json.dumps(
                            {
                                "classification": "OCR_REQUIRED",
                                "pdf_text": ocr_text,
                                "tables": [{"headers": ["Date", "Description", "Ref", "Details", "Debit Amt", "Credit Amt", "Balance"], "rows": ocr_rows}],
                                "errors": [],
                            },
                            ensure_ascii=False,
                        )

                    return json.dumps(
                        {
                            "classification": "OCR_REQUIRED",
                            "pdf_text": ocr_text,
                            "tables": [],
                            "errors": ["OCR completed but no structured table was detected."],
                        },
                        ensure_ascii=False,
                    )

                return json.dumps(
                    {
                        "classification": "NATIVE_TEXT",
                        "pdf_text": native_text,
                        "tables": [],
                        "errors": ["No structured table was found in the native PDF text."],
                    },
                    ensure_ascii=False,
                )

        except FileNotFoundError:
            return json.dumps({
                "tables": [],
                "classification": "ERROR",
                "errors": [f"File not found: {pdf_file_path}"],
            })
        except Exception as exc:
            return json.dumps({
                "tables": [],
                "classification": "ERROR",
                "errors": [f"An error occurred while reading the PDF: {str(exc)}"],
            })
