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

                # If native extraction found no structured table, fall back to OCR when available.
                if classification != "OCR_REQUIRED":
                    classification = "OCR_REQUIRED"

                if not ocr_available:
                    return json.dumps({
                        "classification": classification,
                        "pdf_text": native_text,
                        "tables": [],
                        "errors": [
                            "No structured table was found in the native PDF text and OCR dependencies are missing. "
                            "Install pytesseract, pdf2image, and Pillow, or provide a PDF with a text layer."
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
                    elif os.name == "nt":
                        print(
                            "Warning: TESSERACT_CMD is not set. "
                            "Attempting to use the default tesseract executable on PATH."
                        )

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
                date_pattern = re.compile(
                    r"^(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})",
                    re.IGNORECASE,
                )
                header_line = ""
                for i, line in enumerate(ocr_lines):
                    lower_line = line.lower()
                    if "debit" in lower_line and "credit" in lower_line and "balance" in lower_line:
                        header_index = i
                        header_line = lower_line
                        break

                has_details = False
                ocr_headers = ["Date", "Description", "Ref", "Debit Amt", "Credit Amt", "Balance"]
                if header_index is not None:
                    has_details = "detail" in header_line
                    if has_details:
                        ocr_headers = ["Date", "Description", "Ref", "Details", "Debit Amt", "Credit Amt", "Balance"]

                    number_pattern = re.compile(r"[\d,]+\.\d{2}")
                    prev_balance: float | None = None
                    for line in ocr_lines[header_index + 1:]:
                        raw_line = line.strip()
                        if not raw_line or not date_pattern.match(raw_line):
                            continue
                        numbers = number_pattern.findall(raw_line)
                        if len(numbers) < 2:
                            continue
                        balance_str = numbers[-1].replace(",", "")
                        amount_str = numbers[-2].replace(",", "")
                        try:
                            balance = float(balance_str)
                            amount_value = float(amount_str)
                        except ValueError:
                            continue

                        tokens = raw_line.split()
                        date = ""
                        content_tokens: list[str] = []
                        if len(tokens) >= 2 and re.match(
                            r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)$",
                            tokens[0],
                            re.IGNORECASE,
                        ) and re.match(r"^\d{1,2}$", tokens[1]):
                            date = f"{tokens[0].upper()} {tokens[1]}"
                            content_tokens = tokens[2:]
                        elif tokens and re.match(
                            r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{1,2}$",
                            tokens[0],
                            re.IGNORECASE,
                        ):
                            date = re.sub(r"([A-Za-z]+)(\d{1,2})", r"\1 \2", tokens[0]).upper()
                            content_tokens = tokens[1:]
                        elif tokens and re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$", tokens[0]):
                            date = tokens[0]
                            content_tokens = tokens[1:]
                        else:
                            content_tokens = tokens[1:]

                        # Drop trailing numeric tokens that represent amount and balance
                        while content_tokens and number_pattern.fullmatch(content_tokens[-1].replace(",", "")):
                            content_tokens.pop()
                        while content_tokens and content_tokens[-1] in {"|", "|||", "||", "\uFFFD"}:
                            content_tokens.pop()

                        cleaned_parts = [token for token in content_tokens if token.strip() and token != "|"]
                        cleaned_parts = [token for token in cleaned_parts if not re.fullmatch(r"[\|\uFFFD]+", token)]
                        ref = ""
                        description = ""
                        ref_index = None
                        for idx in range(len(cleaned_parts) - 1, -1, -1):
                            if re.fullmatch(r"\d{3,}", cleaned_parts[idx]):
                                ref_index = idx
                                break
                        if ref_index is not None:
                            ref = cleaned_parts[ref_index]
                            description = " ".join(cleaned_parts[:ref_index])
                        else:
                            description = " ".join(cleaned_parts)

                        lower_prefix = " ".join(cleaned_parts).lower()
                        debit_amt = ""
                        credit_amt = ""
                        if any(keyword in lower_prefix for keyword in ["credit", "transfer in", "cash in", "deposit"]):
                            credit_amt = amount_str
                        elif any(keyword in lower_prefix for keyword in ["debit", "withdrawal", "cash out", "purchase"]):
                            debit_amt = amount_str
                        elif prev_balance is not None:
                            delta = balance - prev_balance
                            if abs(delta - amount_value) < 0.02:
                                credit_amt = amount_str
                            elif abs(delta + amount_value) < 0.02:
                                debit_amt = amount_str
                            else:
                                debit_amt = amount_str
                        else:
                            debit_amt = amount_str

                        prev_balance = balance
                        if has_details:
                            ocr_rows.append([date, description, ref, "", debit_amt, credit_amt, f"{balance:.2f}"])
                        else:
                            ocr_rows.append([date, description, ref, debit_amt, credit_amt, f"{balance:.2f}"])

                if ocr_rows:
                    # Clean non-numeric debit/credit entries before returning
                    numeric_pattern = re.compile(r"^[\d,]+\.\d{2}$")
                    cleaned_rows: list[list[str]] = []
                    for r in ocr_rows:
                        if has_details:
                            date, description, ref, details, debit_amt, credit_amt, balance = (r + [""] * 7)[:7]
                        else:
                            date, description, ref, debit_amt, credit_amt, balance = (r + [""] * 6)[:6]
                            details = ""
                        debit_amt = (debit_amt or "").strip()
                        credit_amt = (credit_amt or "").strip()
                        if debit_amt and not numeric_pattern.match(debit_amt):
                            description = (description + " " + debit_amt).strip()
                            debit_amt = ""
                        if credit_amt and not numeric_pattern.match(credit_amt):
                            description = (description + " " + credit_amt).strip()
                            credit_amt = ""
                        row = [
                            (date or "").strip(),
                            (description or "").strip(),
                            (ref or "").strip(),
                        ]
                        if has_details:
                            row.append((details or "").strip())
                        row.extend([debit_amt, credit_amt, (balance or "").strip()])
                        cleaned_rows.append(row)

                    return json.dumps(
                        {
                            "classification": "OCR_REQUIRED",
                            "pdf_text": ocr_text,
                            "tables": [{"headers": ocr_headers, "rows": cleaned_rows}],
                            "errors": [],
                        },
                        ensure_ascii=False,
                    )

                # Fallback for scanned statements where DATE/DESCRIPTION/REF and
                # DETAILS appear in a left-column OCR block, while DEB AMT / CREDIT AMT /
                # BALANCE appear in a right-column OCR block read separately.
                # Strategy: detect section boundaries by keyword, collect exactly N balance
                # values (N = parsed row count) from the right block, then use
                # balance-delta math to classify each row as debit or credit without
                # relying on fragile per-row index alignment.
                if not ocr_rows:
                    table_header_index = next(
                        (
                            i
                            for i, line in enumerate(ocr_lines)
                            if "date" in line.lower() and "description" in line.lower() and "ref" in line.lower()
                        ),
                        None,
                    )
                    debit_header_index = next(
                        (
                            i
                            for i, line in enumerate(ocr_lines)
                            if "deb" in line.lower() and "amt" in line.lower()
                        ),
                        None,
                    )
                    credit_balance_index = next(
                        (
                            i
                            for i, line in enumerate(ocr_lines)
                            if "credit" in line.lower() and "balance" in line.lower()
                        ),
                        None,
                    )

                    if table_header_index is not None and debit_header_index is not None and credit_balance_index is not None:
                        # Detect optional DETAILS header sitting between the DATE and DEB AMT headers
                        details_header_index = next(
                            (
                                i
                                for i, line in enumerate(ocr_lines)
                                if table_header_index < i < debit_header_index
                                and re.fullmatch(r"details?", line.strip(), re.IGNORECASE)
                            ),
                            None,
                        )

                        # ── 1. Parse LEFT section: DATE / DESCRIPTION / REF ──────────────
                        left_end = details_header_index if details_header_index is not None else debit_header_index
                        left_section = ocr_lines[table_header_index + 1 : left_end]
                        date_line_pat = re.compile(
                            r"^(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
                            r"|(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*\d{1,2})",
                            re.IGNORECASE,
                        )

                        left_rows: list[tuple[str, str, str]] = []
                        for line in left_section:
                            stripped = line.strip()
                            if not stripped:
                                continue
                            # Balance carry-forward row (e.g. "PREVIOUS BALANCE")
                            if re.search(
                                r"(?:previous|beginning|beg\.?)\s+balance",
                                stripped,
                                re.IGNORECASE,
                            ):
                                left_rows.append(("", stripped, ""))
                                continue
                            if not date_line_pat.match(stripped):
                                continue
                            parts = stripped.split()
                            date = ""
                            remaining = parts
                            if (
                                len(parts) >= 2
                                and re.match(r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)$", parts[0], re.IGNORECASE)
                                and re.match(r"^\d{1,2}$", parts[1])
                            ):
                                date = f"{parts[0].upper()} {parts[1]}"
                                remaining = parts[2:]
                            elif parts and re.match(
                                r"^(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{1,2}$",
                                parts[0],
                                re.IGNORECASE,
                            ):
                                m_date = re.match(r"^([A-Za-z]+)(\d{1,2})$", parts[0])
                                if m_date:
                                    date = f"{m_date.group(1).upper()} {m_date.group(2)}"
                                    remaining = parts[1:]
                            elif parts and re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$", parts[0]):
                                date = parts[0]
                                remaining = parts[1:]
                            ref = ""
                            if remaining and re.fullmatch(r"\d{3,}", remaining[-1]):
                                ref = remaining[-1]
                                description = " ".join(remaining[:-1])
                            else:
                                description = " ".join(remaining)
                            description = re.sub(r"^[=|]+\s*", "", description).strip()
                            left_rows.append((date, description, ref))

                        # ── 2. Parse DETAILS section (sequential best-effort) ────────────
                        detail_lines: list[str] = []
                        if details_header_index is not None:
                            raw_detail_block = ocr_lines[details_header_index + 1 : debit_header_index]
                            detail_lines = [ln.strip() for ln in raw_detail_block if ln.strip()]

                        # ── 3. Anchor balance values to row count ─────────────────────────
                        # In split-column statements the right OCR block emits all N balance
                        # values contiguously first, then credit-only amounts for credit rows.
                        # Taking the first N numbers gives exactly one balance per row.
                        n_rows = len(left_rows)
                        amt_pat = re.compile(r"[\d,]+\.\d{2}")
                        all_right_numbers: list[str] = []
                        for line in ocr_lines[credit_balance_index + 1 :]:
                            for m_num in amt_pat.finditer(line):
                                try:
                                    float(m_num.group().replace(",", ""))
                                    all_right_numbers.append(m_num.group())
                                except ValueError:
                                    pass

                        balance_strs = all_right_numbers[:n_rows]

                        if len(balance_strs) == n_rows:
                            balances = [float(b.replace(",", "")) for b in balance_strs]

                            # ── 4. Build output rows via balance-delta classification ──────
                            # delta > 0  →  credit row   (balance went up)
                            # delta < 0  →  debit  row   (balance went down)
                            # delta ≈ 0  →  treat as debit with zero amount (edge case)
                            detail_idx = 0
                            for i, (date, description, ref) in enumerate(left_rows):
                                balance_str = balance_strs[i]
                                is_carry = not date  # carry-forward rows have no date token

                                if is_carry:
                                    deb_amt = ""
                                    credit_amt = ""
                                    detail = ""
                                else:
                                    delta = balances[i] - balances[i - 1]
                                    if delta > 0.005:
                                        credit_amt = f"{delta:,.2f}"
                                        deb_amt = ""
                                    else:
                                        deb_amt = f"{abs(delta):,.2f}"
                                        credit_amt = ""
                                    detail = (
                                        detail_lines[detail_idx]
                                        if detail_idx < len(detail_lines)
                                        else ""
                                    )
                                    detail_idx += 1

                                ocr_rows.append(
                                    [date, description, ref, detail, deb_amt, credit_amt, balance_str]
                                )

                if ocr_rows:
                    # Final cleanup: ensure debit cells are numeric; move text into description
                    numeric_pattern = re.compile(r"^[\d,]+\.\d{2}$")
                    cleaned_rows: list[list[str]] = []
                    for r in ocr_rows:
                        date, description, ref, details, debit_amt, credit_amt, balance = (r + [""] * 7)[:7]
                        debit_amt = (debit_amt or "").strip()
                        credit_amt = (credit_amt or "").strip()
                        if debit_amt and not numeric_pattern.match(debit_amt):
                            description = (description + " " + debit_amt).strip()
                            debit_amt = ""
                        cleaned_rows.append([
                            (date or "").strip(),
                            (description or "").strip(),
                            (ref or "").strip(),
                            (details or "").strip(),
                            debit_amt,
                            credit_amt,
                            (balance or "").strip(),
                        ])

                    return json.dumps(
                        {
                            "classification": "OCR_REQUIRED",
                            "pdf_text": ocr_text,
                            "tables": [{"headers": ["Date", "Description", "Ref", "Details", "Debit Amt", "Credit Amt", "Balance"], "rows": cleaned_rows}],
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
