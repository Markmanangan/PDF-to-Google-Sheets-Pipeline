import json
import os
import re
from pathlib import Path
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

    Uses pdfplumber for text extraction, then passes the raw text to the Groq
    LLM for dynamic, AI-powered table detection and column mapping. Works with
    any bank format (BPI, BDO, Metrobank, UnionBank, etc.) without hardcoded rules.
    """

    name: str = "PDFTableExtractorTool"
    description: str = (
        "Extracts bank statement transaction tables from a PDF file using AI. "
        "Dynamically detects columns and values regardless of bank format. "
        "Returns structured JSON with standardized headers and rows."
    )
    args_schema: Type[BaseModel] = PDFTableExtractorInput

    def _run(self, pdf_file_path: str) -> str:
        """
        Opens the PDF at pdf_file_path, extracts raw text using pdfplumber,
        then uses the Groq LLM to dynamically parse the transaction table.

        Falls back to OCR if the PDF has no native text layer.

        Returns structured JSON containing:
          - classification: NATIVE_TEXT | OCR_REQUIRED | ERROR
          - pdf_text: extracted text
          - tables: extracted tables
          - errors: optional errors list
        """
        # -----------------------------------------------------------------------
        # 1. Lazy imports
        # -----------------------------------------------------------------------
        try:
            import pdfplumber
        except ImportError:
            return json.dumps({
                "tables": [],
                "classification": "ERROR",
                "errors": ["pdfplumber is not installed. Run: pip install pdfplumber"],
            })

        ocr_available = True
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            from pdf2image import convert_from_path  # type: ignore
        except ImportError:
            ocr_available = False

        # -----------------------------------------------------------------------
        # 2. Extract raw text from PDF
        # -----------------------------------------------------------------------
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
                page_has_text = any(page.extract_text() for page in pages)

                classification = "NATIVE_TEXT"
                if not page_has_text or native_word_count < 12:
                    classification = "OCR_REQUIRED"

                raw_text = native_text

                # ---------------------------------------------------------------
                # 3. OCR fallback if no native text layer
                # ---------------------------------------------------------------
                if classification == "OCR_REQUIRED":
                    if not ocr_available:
                        return json.dumps({
                            "classification": "OCR_REQUIRED",
                            "pdf_text": native_text,
                            "tables": [],
                            "errors": [
                                "No text found in PDF and OCR dependencies are missing. "
                                "Install pytesseract, pdf2image, and Pillow."
                            ],
                        })

                    try:
                        poppler_path = os.getenv("POPPLER_PATH")
                        tesseract_cmd = os.getenv("TESSERACT_CMD")

                        if not tesseract_cmd and os.name == "nt":
                            common_tesseract_paths = [
                                Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                                Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
                                Path.home() / r"AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
                            ]
                            for candidate in common_tesseract_paths:
                                if candidate.exists():
                                    tesseract_cmd = str(candidate)
                                    break

                        if not poppler_path and os.name == "nt":
                            repo_root = Path(__file__).resolve().parents[3]
                            local_poppler = next(
                                (
                                    candidate
                                    for candidate in repo_root.glob("poppler-*/Library/bin")
                                    if candidate.exists()
                                ),
                                None,
                            )
                            if local_poppler is not None:
                                poppler_path = str(local_poppler)

                        convert_kwargs = {"dpi": 300}
                        if poppler_path:
                            convert_kwargs["poppler_path"] = poppler_path
                            if not Path(poppler_path).exists():
                                raise FileNotFoundError(
                                    f"POPPLER_PATH is set to '{poppler_path}' but the folder does not exist."
                                )

                        images = convert_from_path(pdf_file_path, **convert_kwargs)

                        if tesseract_cmd:
                            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

                        ocr_text_pages = [pytesseract.image_to_string(image) for image in images]
                        raw_text = "\n\n".join(ocr_text_pages).strip()

                    except Exception as exc:
                        return json.dumps({
                            "classification": "OCR_FAILED",
                            "pdf_text": native_text,
                            "tables": [],
                            "errors": [f"OCR conversion failed: {exc}"],
                        })

                # ---------------------------------------------------------------
                # 4. Extraction strategy: structured table (native) or AI (OCR)
                # ---------------------------------------------------------------
                if not raw_text.strip():
                    return json.dumps({
                        "classification": classification,
                        "pdf_text": "",
                        "tables": [],
                        "errors": ["Could not extract any text from the PDF."],
                    })

                # For native-text PDFs try pdfplumber's structured table extractor first.
                # It uses whitespace geometry to reconstruct columns accurately.
                pdfplumber_result = None
                if classification == "NATIVE_TEXT":
                    with pdfplumber.open(pdf_file_path) as pdf2:
                        all_pdfplumber_rows: list[list[str]] = []
                        detected_headers: list[str] = []
                        for pg in pdf2.pages:
                            raw_tables = pg.extract_tables() or []
                            for tbl in raw_tables:
                                if not tbl:
                                    continue
                                # First non-empty row is the header
                                for i, row in enumerate(tbl):
                                    clean = [str(c).strip() if c else "" for c in row]
                                    if any(clean):  # skip totally empty rows
                                        if not detected_headers:
                                            detected_headers = clean
                                        else:
                                            all_pdfplumber_rows.append(clean)
                    if detected_headers and all_pdfplumber_rows:
                        # Pad/trim every row to match header length
                        n = len(detected_headers)
                        clean_rows = []
                        for row in all_pdfplumber_rows:
                            padded = (row + [""] * n)[:n]
                            clean_rows.append(padded)
                        pdfplumber_result = {
                            "tables": [{"headers": detected_headers, "rows": clean_rows}]
                        }

                if pdfplumber_result and pdfplumber_result.get("tables"):
                    ai_result = pdfplumber_result
                else:
                    ai_result = self._extract_with_ai(raw_text, is_ocr=(classification == "OCR_REQUIRED"))

                if ai_result.get("error"):
                    return json.dumps({
                        "classification": classification,
                        "pdf_text": raw_text,
                        "tables": [],
                        "errors": [ai_result["error"]],
                    })

                tables = ai_result.get("tables", [])
                if not tables:
                    return json.dumps({
                        "classification": classification,
                        "pdf_text": raw_text,
                        "tables": [],
                        "errors": ["AI could not detect a transaction table in this document."],
                    })

                return json.dumps({
                    "classification": classification,
                    "pdf_text": raw_text,
                    "tables": tables,
                    "errors": [],
                }, ensure_ascii=False)

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

    def _extract_with_ai(self, raw_text: str, is_ocr: bool = False) -> dict:
        """
        Sends raw PDF text to the Groq LLM and asks it to dynamically detect
        the transaction table columns and extract all rows into a standardized
        JSON structure, regardless of the bank format or column naming.
        """
        try:
            import litellm  # type: ignore
        except ImportError:
            return {"error": "litellm is not installed. Run: pip install litellm"}

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return {"error": "GROQ_API_KEY is not set in your .env file."}

        if is_ocr:
            prompt = f"""You are an expert bank statement parser. This text was extracted via OCR from a SCANNED bank statement image.

IMPORTANT: Scanned bank statements often have a SPLIT-COLUMN layout, meaning the OCR text reads:
1. First: All rows of the LEFT section (Date, Description, Reference columns)
2. Then: All rows of the RIGHT section (Details, Debit, Credit, Balance columns)

These two sections belong to the SAME table. You must STITCH them together by matching rows positionally (row 1 left + row 1 right = first transaction, row 2 left + row 2 right = second transaction, etc.).

Step 1 — Identify the LEFT section header (e.g. "DATE DESCRIPTION REF") and all its rows.
Step 2 — Identify the RIGHT section header (e.g. "DETAILS DEBIT AMT CREDIT AMT BALANCE") and all its rows.
Step 3 — Combine: merged header = left headers + right headers. Each merged row = left row values + right row values.
Step 4 — Use the EXACT column names as they appear in the scanned text.

Return ONLY a valid JSON object in this exact format — no extra text, no markdown, no code blocks:
{{
    "headers": ["<exact col from left section>", ..., "<exact col from right section>", ...],
    "rows": [
        ["left_val1", "left_val2", ..., "right_val1", "right_val2", ...],
        ...
    ]
}}

Rules:
- Use the EXACT column names from the scanned text
- Skip non-transaction rows: table header rows, blank rows, "PREVIOUS BALANCE" carry-forward rows, totals
- Include EVERY transaction row even if some cells are empty — use "" for empty cells
- Amount values must be numeric strings: "1,234.56" — no currency symbols
- Each merged row must have the same number of values as there are merged headers

Full OCR text:
{raw_text}

Return ONLY the JSON object:"""
        else:
            prompt = f"""You are an expert bank statement parser. Your job is to extract ALL transaction rows from the raw text of a bank statement PDF.

The bank statement may be from ANY bank (BPI, BDO, Metrobank, UnionBank, etc.) with ANY column layout.

Step 1 — Find the transaction table header row (e.g. "Date", "Description", "Debit", "Credit", "Balance", "Withdrawal", "Deposit", "Narration", "Reference", etc.)
Step 2 — Use those EXACT column names as the "headers" in your JSON output.
Step 3 — Extract every data row under that table.

Return ONLY a valid JSON object in this exact format — no extra text, no markdown, no code blocks:
{{
    "headers": ["<exact column name from PDF>", "<exact column name from PDF>", ...],
    "rows": [
        ["value1", "value2", ...],
        ...
    ]
}}

Rules:
- Use the EXACT column names as they appear in the PDF (e.g. if the PDF says "Withdrawal", use "Withdrawal" not "Debit")
- If a column name spans multiple words, keep them as-is (e.g. "Transaction Description", "Posting Date")
- Skip non-transaction rows: table headers, blank rows, page footers, subtotals, opening/closing balance summary lines
- Include EVERY transaction row even if some cells are empty — use "" for empty cells
- Amount values must be numeric strings with commas and decimals: "1,234.56" — no currency symbols (no PHP, $, etc.)
- Each row must have the same number of values as there are headers

Raw bank statement text:
{raw_text[:12000]}

Return ONLY the JSON object:"""


        try:
            response = litellm.completion(
                model="groq/llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                temperature=0.0,
                max_tokens=4096,
            )
            content = response.choices[0].message.content.strip()

            # Strip markdown code blocks if the LLM wrapped the JSON
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            content = content.strip()

            parsed = json.loads(content)

            headers = parsed.get("headers", ["Date", "Description", "Reference", "Debit Amt", "Credit Amt", "Balance"])
            rows = parsed.get("rows", [])

            if not rows:
                return {"tables": []}

            # Normalize all row values to strings
            clean_rows = []
            for row in rows:
                clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                # Pad row to match header length
                while len(clean_row) < len(headers):
                    clean_row.append("")
                clean_rows.append(clean_row[:len(headers)])

            return {"tables": [{"headers": headers, "rows": clean_rows}]}

        except json.JSONDecodeError as e:
            return {"error": f"AI returned invalid JSON: {e}"}
        except Exception as e:
            return {"error": f"AI extraction failed: {str(e)}"}
