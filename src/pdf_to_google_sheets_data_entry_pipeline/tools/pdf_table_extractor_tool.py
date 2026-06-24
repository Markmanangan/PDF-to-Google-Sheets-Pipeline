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
                # 4. AI-powered dynamic extraction
                # ---------------------------------------------------------------
                if not raw_text.strip():
                    return json.dumps({
                        "classification": classification,
                        "pdf_text": "",
                        "tables": [],
                        "errors": ["Could not extract any text from the PDF."],
                    })

                ai_result = self._extract_with_ai(raw_text)

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

    def _extract_with_ai(self, raw_text: str) -> dict:
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

        prompt = f"""You are an expert bank statement parser. Your job is to extract ALL transaction rows from the raw text of a bank statement PDF.

The bank statement may be from ANY bank (BPI, BDO, Metrobank, UnionBank, etc.) with ANY column layout.

Return ONLY a valid JSON object in this exact format — no extra text, no markdown, no code blocks:
{{
    "headers": ["Date", "Description", "Reference", "Debit Amt", "Credit Amt", "Balance"],
    "rows": [
        ["date", "description", "reference", "debit_amount", "credit_amount", "balance"],
        ...
    ]
}}

Rules for extracting:
- Date: the transaction date exactly as shown (e.g. "06/15/2024", "JUN 15", "15-Jun-2024")
- Description: the transaction name or narration (e.g. "CASH WITHDRAWAL", "ONLINE TRANSFER", "PAYROLL")
- Reference: reference/transaction number if shown, otherwise use ""
- Debit Amt: amount withdrawn/debited as a numeric string (e.g. "1,500.00"), or "" if this is a credit/deposit
- Credit Amt: amount deposited/credited as a numeric string (e.g. "5,000.00"), or "" if this is a debit/withdrawal
- Balance: the running balance after this transaction as a numeric string (e.g. "12,345.67")

Important rules:
- Map column names intelligently: "Withdrawal" = Debit, "Deposit" = Credit, "Posting Date" = Date, "Narration" = Description
- Skip non-transaction rows: headers, subtotals, opening balance summaries, blank rows, page footers
- Include EVERY transaction row, even if some fields are empty
- Amounts must be plain numbers with commas and decimals: "1,234.56" — do NOT include currency symbols

Raw bank statement text:
{raw_text[:8000]}

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
