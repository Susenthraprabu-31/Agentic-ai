"""Mistral Document AI OCR for dynamic webpage screenshot extraction and structuring."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"


def clean_text(text: str | None) -> str:
    """Normalize whitespace and strip markdown/LaTeX formatting artifacts."""
    if not text:
        return ""
    # Strip LaTeX textcircled and circled numbers
    cleaned = re.sub(r"\\textcircled\{?[^}]*\}?", "", text)
    cleaned = re.sub(r"[①②③④⑤⑥⑦⑧⑨⑩]", "", cleaned)
    # Strip LaTeX math tokens \( ... \) or \[ ... \]
    cleaned = re.sub(r"\\\(|\\\)", "", cleaned)
    cleaned = re.sub(r"\\\[|\\\]", "", cleaned)
    # Clean escaped dollar signs and markdown symbols
    cleaned = cleaned.replace(r"\$", "$")
    cleaned = re.sub(r"[*_`]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_numeric_value(val: str | None) -> float | None:
    """Extract numeric value from currency or decimal strings ($123,456.78 -> 123456.78)."""
    if not val:
        return None
    cleaned = clean_text(val).replace("$", "").replace(",", "").strip()
    m = re.search(r"[-+]?\d*\.?\d+", cleaned)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


class MistralWebpageOCRService:
    """
    High-accuracy visual webpage extractor using Mistral Document AI OCR.
    Extracts complete, un-truncated property cards, tables, and attributes directly from
    full-page screenshots across any county portal.
    """

    def __init__(self) -> None:
        self.settings = settings

    def is_enabled(self) -> bool:
        """Returns True if Mistral OCR is enabled and API key is present."""
        return bool(
            getattr(self.settings, "MISTRAL_OCR_ENABLED", True)
            and (getattr(self.settings, "MISTRAL_API_KEY", "") or "").strip()
        )

    async def extract_from_page(
        self,
        page: Any,
        query_value: str = "",
        scroll_first: bool = True,
    ) -> dict[str, Any]:
        """
        Captures a high-resolution full-page screenshot of the active Playwright page,
        sends it to Mistral OCR API, and parses the returned layout-grounded Markdown
        into structured property fields and tables.
        """
        if not self.is_enabled():
            logger.info("Mistral OCR is not enabled or MISTRAL_API_KEY is not configured.")
            return {}

        try:
            # 1. Trigger lazy-loaded page elements by smooth auto-scrolling
            if scroll_first and hasattr(page, "evaluate"):
                try:
                    await page.evaluate("""async () => {
                        await new Promise((resolve) => {
                            let totalHeight = 0;
                            const distance = 400;
                            const timer = setInterval(() => {
                                const scrollHeight = document.body.scrollHeight;
                                window.scrollBy(0, distance);
                                totalHeight += distance;
                                if (totalHeight >= scrollHeight || totalHeight > 12000) {
                                    clearInterval(timer);
                                    window.scrollTo(0, 0);
                                    resolve();
                                }
                            }, 80);
                        });
                    }""")
                    await page.wait_for_timeout(400)
                except Exception as scroll_err:
                    logger.debug("Auto-scroll before screenshot note: %s", scroll_err)

            # 2. Capture full-page screenshot
            screenshot_bytes = await page.screenshot(full_page=True, type="png")
            if not screenshot_bytes or len(screenshot_bytes) < 1000:
                logger.warning("Captured screenshot is empty or too small (%d bytes)", len(screenshot_bytes) if screenshot_bytes else 0)
                return {}

            logger.info("Captured full-page screenshot (%d bytes) for Mistral OCR analysis", len(screenshot_bytes))

            # 3. Call Mistral OCR API
            return await self.extract_from_image_bytes(
                image_bytes=screenshot_bytes,
                mime_type="image/png",
                query_value=query_value,
            )
        except Exception as exc:
            logger.warning("Mistral OCR page extraction failed: %s", exc)
            return {}

    async def extract_from_image_bytes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
        query_value: str = "",
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Sends an image or PDF buffer directly to the Mistral OCR API."""
        return await self.extract_from_document_bytes(
            doc_bytes=image_bytes,
            mime_type=mime_type,
            query_value=query_value,
            timeout_seconds=timeout_seconds,
        )

    async def extract_from_document_bytes(
        self,
        doc_bytes: bytes,
        mime_type: str = "application/pdf",
        query_value: str = "",
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Sends a PDF or image document buffer directly to the Mistral Document AI OCR API."""
        if not self.is_enabled():
            return {}

        api_key = (getattr(self.settings, "MISTRAL_API_KEY", "") or "").strip()
        model = getattr(self.settings, "MISTRAL_OCR_MODEL", "mistral-ocr-2512") or "mistral-ocr-2512"

        is_pdf = mime_type == "application/pdf" or doc_bytes[:5].startswith(b"%PDF")
        actual_mime = "application/pdf" if is_pdf else (mime_type or "image/png")

        b64_data = base64.b64encode(doc_bytes).decode("ascii")
        data_url = f"data:{actual_mime};base64,{b64_data}"

        doc_payload: dict[str, Any]
        if is_pdf:
            doc_payload = {
                "type": "document_url",
                "document_url": data_url,
            }
        else:
            doc_payload = {
                "type": "image_url",
                "image_url": data_url,
            }

        payload = {
            "model": model,
            "document": doc_payload,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            resp = await client.post(_MISTRAL_OCR_URL, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.warning("Mistral OCR returned HTTP %d: %s", resp.status_code, resp.text[:300])
                return {}

            res_json = resp.json()

        # Combine all markdown pages
        pages = res_json.get("pages") or []
        md_parts = []
        page_mds = []
        for p in pages:
            if isinstance(p, dict) and p.get("markdown"):
                md = p["markdown"]
                md_parts.append(md)
                page_mds.append(md)

        combined_md = "\n\n".join(md_parts).strip()
        if not combined_md:
            logger.warning("Mistral OCR completed but returned no markdown text.")
            return {}

        parsed_data = self.parse_ocr_markdown(combined_md, query_value=query_value)

        # Apply Tightened Mistral AI Document Structuring
        try:
            ai_data = await self.structure_markdown_with_ai(combined_md, query_value=query_value)
            if ai_data and isinstance(ai_data, dict):
                for k in [
                    "parcel_number", "account_number", "owner_name", "property_address",
                    "mailing_address", "property_use", "taxing_district", "land_size",
                    "exemptions", "map_number", "just_value", "assessed_value", "taxable_value",
                    "tax_year", "tax_status", "total_amount_due", "current_bill_year", "is_delinquent"
                ]:
                    val = ai_data.get(k)
                    if val is not None and val != "":
                        parsed_data[k] = val

                # Format land_size if dict with value/unit
                if isinstance(parsed_data.get("land_size"), dict):
                    ls = parsed_data["land_size"]
                    val_num = ls.get("value", "")
                    val_unit = ls.get("unit", ls.get("units", ""))
                    parsed_data["land_size"] = f"{val_num} {val_unit}".strip() if (val_num or val_unit) else ""

                # Format exemptions if dict or list
                if isinstance(parsed_data.get("exemptions"), dict):
                    ex = parsed_data["exemptions"]
                    non_zero = [f"{ek}: {ev}" for ek, ev in ex.items() if ev and ev != 0 and ev != "0" and ev != "None"]
                    parsed_data["exemptions"] = ", ".join(non_zero) if non_zero else ", ".join(str(ek) for ek in ex.keys())
                elif isinstance(parsed_data.get("exemptions"), list):
                    parsed_data["exemptions"] = ", ".join(str(e) for e in parsed_data["exemptions"])

                # Clean structured tables from AI
                ai_tables = ai_data.get("tables")
                if ai_tables and isinstance(ai_tables, list) and len(ai_tables) > 0:
                    parsed_data["raw_tables"] = ai_tables

                # Merge all key values
                ai_kvs = ai_data.get("all_key_values")
                if ai_kvs and isinstance(ai_kvs, dict):
                    current_kvs = parsed_data.get("all_key_values", {})
                    current_kvs.update(ai_kvs)
                    parsed_data["all_key_values"] = current_kvs
        except Exception as struct_err:
            logger.warning("Mistral AI document structuring note: %s", struct_err)

        parsed_data["ocr_engine"] = "mistral-ocr"
        parsed_data["ocr_model"] = model
        parsed_data["pages_count"] = len(pages)
        parsed_data["pages_markdown"] = page_mds
        return parsed_data

    async def structure_markdown_with_ai(
        self,
        markdown: str,
        query_value: str = "",
        timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        """
        Use Mistral LLM with a strictly tightened prompt to structure the raw OCR markdown
        into property identity, tax fields, and clean tables.
        """
        api_key = (getattr(self.settings, "MISTRAL_API_KEY", "") or "").strip()
        chat_model = getattr(self.settings, "MISTRAL_CHAT_MODEL", "mistral-large-latest") or "mistral-large-latest"

        prompt = f"""You are an expert title search and property document analyzer.
Extract the structured property record, owner, addresses, values, and tax details from the OCR Markdown text below with 100% precision.

OCR MARKDOWN:
{markdown}

Extract and return ONLY a valid JSON object matching this schema:
{{
  "parcel_number": "The parcel ID, folio number, or real estate account number (e.g. 30-6924-001-0030)",
  "account_number": "Tax account number if different from parcel number, otherwise parcel number",
  "owner_name": "Full owner of record name (e.g. JUAN A MORALES)",
  "property_address": "Full site / situs property address with city, state, zip",
  "mailing_address": "Mailing address if present, otherwise null",
  "property_use": "Property use code or zoning description if present, otherwise null",
  "taxing_district": "Taxing district if present, otherwise null",
  "land_size": "Land size / acreage if present, otherwise null",
  "exemptions": "Exemptions if present (e.g. Homestead Exemption), otherwise null",
  "map_number": "Map or plat number if present, otherwise null",
  "just_value": null,
  "assessed_value": null,
  "taxable_value": null,
  "tax_year": 2024,
  "tax_status": "Unpaid, Paid, or Delinquent",
  "total_amount_due": null,
  "current_bill_year": "",
  "is_delinquent": false,
  "tables": [
    {{
      "name": "Account History or Table Name",
      "headers": ["BILL", "AMOUNT DUE", "STATUS", "PAYMENT DATE", "RECEIPT"],
      "rows": [
        ["2024 Annual Bill", "$7,280.52", "Unpaid", "", ""]
      ]
    }}
  ],
  "all_key_values": {{
    "Account Number": "...",
    "Owner": "...",
    "Situs": "...",
    "Amount Due": "...",
    "Status": "..."
  }}
}}
Rules:
1. Strip all LaTeX math artifacts (like \\textcircled{{1}}, \\(, \\)) from table cells and text.
2. If the document has 'Real Estate Account #<number>', use that as parcel_number and account_number.
3. If 'Owner:' is followed by a name on the next line or same line, capture that full name as owner_name.
4. If 'Situs:' is followed by an address, capture that full address as property_address.
5. In tables, ensure columns match their respective headers without offset.
"""

        # 1. Try Mistral Chat API
        if api_key:
            try:
                payload = {
                    "model": chat_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a professional real estate and property document parser. Respond strictly in valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                }
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
                    resp = await client.post(
                        "https://api.mistral.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        import json
                        ai_data = json.loads(content)
                        logger.info("Mistral LLM (%s) structured OCR data successfully", chat_model)
                        return ai_data
                    else:
                        logger.warning("Mistral Chat API returned HTTP %d: %s", resp.status_code, resp.text[:200])
            except Exception as m_err:
                logger.warning("Mistral Chat API call error: %s", m_err)

        # 2. Fallback to OpenAI / Groq LLM if configured
        try:
            from app.core.llm_client import chat_completions_create, llm_configured
            if llm_configured():
                resp, m_used = await chat_completions_create(
                    create_kwargs={
                        "model": getattr(self.settings, "OPENAI_MODEL", "gpt-6-luna") or "gpt-6-luna",
                        "messages": [
                            {"role": "system", "content": "You are a professional real estate and property document parser. Respond strictly in valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    }
                )
                import json
                content = resp.choices[0].message.content or "{}"
                ai_data = json.loads(content)
                logger.info("LLM fallback (%s) structured OCR data successfully", m_used)
                return ai_data
        except Exception as llm_err:
            logger.warning("LLM fallback structuring error: %s", llm_err)

        return {}

    def parse_ocr_markdown(self, markdown: str, query_value: str = "") -> dict[str, Any]:
        """
        Parses Mistral OCR markdown tables and text blocks into a normalized property record.
        Extracts parcel ID, owners, mailing & site addresses, land size, values, exemptions,
        and all tabular data (sales, valuations).
        """
        lines = [line.strip() for line in markdown.split("\n") if line.strip()]

        key_values: dict[str, str] = {}
        tables: list[dict[str, Any]] = []

        # 1. Parse Markdown Tables
        current_table_headers: list[str] = []
        current_table_rows: list[list[str]] = []

        for line in lines:
            if line.startswith("|") and line.endswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                # Header separator line (e.g. | --- | --- |)
                if all(re.match(r"^:?-+:?$", cell) for cell in cells if cell):
                    continue

                if not current_table_headers:
                    current_table_headers = [clean_text(c) for c in cells]
                else:
                    # Sanitize row cells: if Mistral inserted a standalone footnote badge cell, filter it
                    cleaned_row = [clean_text(c) for c in cells]
                    if len(cleaned_row) > len(current_table_headers):
                        filtered = [c for c in cleaned_row if c and not re.match(r"^(\(?\d+\)?|[①-⑩*†‡])$", c)]
                        if len(filtered) == len(current_table_headers):
                            cleaned_row = filtered
                    current_table_rows.append(cleaned_row)
            else:
                if current_table_headers and current_table_rows:
                    tables.append({
                        "headers": current_table_headers,
                        "rows": current_table_rows,
                    })
                    current_table_headers = []
                    current_table_rows = []

        if current_table_headers and current_table_rows:
            tables.append({
                "headers": current_table_headers,
                "rows": current_table_rows,
            })

        # Process tables for 2-column key-value pairs (common in property cards)
        for tbl in tables:
            headers = tbl["headers"]
            rows = tbl["rows"]
            # Check if header row itself is a key-value pair (e.g. | OWNER: | O'KEEFE MORGAN |)
            if len(headers) == 2 and headers[0] and headers[1]:
                hk = headers[0].rstrip(":").lower().strip()
                hv = headers[1].strip()
                if hk and hv and (":" in headers[0] or any(t in hk for t in ["owner", "parcel", "folio", "address", "site", "mail", "district", "use", "exemption", "map", "size", "value"]) or len(hk) <= 30):
                    if hk not in key_values:
                        key_values[hk] = hv
            elif len(headers) >= 4 and headers[0] and headers[1]:
                hk1 = headers[0].rstrip(":").lower().strip()
                hv1 = headers[1].strip()
                if hk1 and hv1 and ":" in headers[0]:
                    if hk1 not in key_values:
                        key_values[hk1] = hv1
                if len(headers) >= 4 and headers[2] and headers[3]:
                    hk2 = headers[2].rstrip(":").lower().strip()
                    hv2 = headers[3].strip()
                    if hk2 and hv2 and ":" in headers[2]:
                        if hk2 not in key_values:
                            key_values[hk2] = hv2

            for row in rows:
                if len(row) == 2 and row[0] and row[1]:
                    k = row[0].rstrip(":").lower().strip()
                    v = row[1].strip()
                    if k and v and k not in key_values:
                        key_values[k] = v
                elif len(row) >= 4 and len(headers) >= 4:
                    # 4-column key-value pairs e.g. [Key1, Val1, Key2, Val2]
                    if row[0] and row[1]:
                        k = row[0].rstrip(":").lower().strip()
                        if k and k not in key_values:
                            key_values[k] = row[1].strip()
                    if row[2] and row[3]:
                        k = row[2].rstrip(":").lower().strip()
                        if k and k not in key_values:
                            key_values[k] = row[3].strip()

        # 2. Parse inline KEY: VALUE patterns from lines
        for line in lines:
            # Skip pure markdown headers without colons or table rows
            if line.startswith("|"):
                continue

            # Look for lines with `KEY: VALUE` or `**KEY:** VALUE`
            m = re.match(
                r"^(?:[#*_-]+\s*)?(?:\*\*)?([A-Za-z0-9\s/()._#-]{2,50}?)(?:\*\*)?:\s*(.+)$",
                line,
            )
            if m:
                raw_k = clean_text(m.group(1)).lower().strip()
                raw_v = clean_text(m.group(2)).strip()
                # Ignore URLs, questions or boilerplate
                if raw_k and raw_v and "http" not in raw_k and "call us" not in raw_k:
                    if raw_k not in key_values:
                        key_values[raw_k] = raw_v

        # 2B. Multi-line pattern matching (e.g. Real Estate Account #..., Owner:\n<name>, Situs:\n<addr>)
        m_re = re.search(r"Real\s*Estate\s*Account\s*#?\s*([0-9\-_]+)", markdown, re.I)
        if m_re:
            key_values["account number"] = m_re.group(1).strip()
            key_values["parcel id"] = m_re.group(1).strip()

        m_ow = re.search(r"Owner:?\s*\n+([^\n]+)", markdown, re.I)
        if m_ow and "http" not in m_ow.group(1):
            key_values["owner"] = clean_text(m_ow.group(1))

        m_si = re.search(r"(?:Situs|Vitae):?\s*\n+([^\n]+(?:\n+[^\n]+)?)", markdown, re.I)
        if m_si and "http" not in m_si.group(1):
            key_values["site address"] = clean_text(m_si.group(1).replace("\n", ", "))

        m_tot = re.search(r"Total\s*Amount\s*Due:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", markdown, re.I)
        if m_tot:
            key_values["amount due"] = m_tot.group(1).strip()

        # 3. Canonical Field Mapping
        record: dict[str, Any] = {
            "parcel_number": None,
            "owner_name": None,
            "property_address": None,
            "mailing_address": None,
            "legal_description": None,
            "property_use": None,
            "taxing_district": None,
            "land_size": None,
            "exemptions": None,
            "map_number": None,
            "just_value": None,
            "assessed_value": None,
            "taxable_value": None,
            "subdivision": None,
            "sales_history": [],
            "valuation_history": [],
            "all_key_values": key_values,
            "raw_tables": tables,
            "raw_markdown": markdown,
        }

        # Match Parcel Number / Folio / APN / PIN / Account Number
        for k, v in key_values.items():
            if any(term in k for term in ["parcel id", "parcel number", "folio", "pin", "apn", "strap", "account number", "account #"]):
                clean_parcel = re.sub(r"^[^\w]+", "", v).strip()
                if clean_parcel and not record["parcel_number"]:
                    record["parcel_number"] = clean_parcel

        # Match Owner Name
        for k, v in key_values.items():
            if any(term in k for term in ["owner name", "owner:", "owners", "owner"]):
                if "mailing" not in k and "previous" not in k and not record["owner_name"]:
                    record["owner_name"] = v

        # Match Site / Property Address
        for k, v in key_values.items():
            if any(term in k for term in ["site address", "property address", "location", "physical address", "situs", "vitae"]):
                if not record["property_address"]:
                    record["property_address"] = v

        # Match Mailing Address
        for k, v in key_values.items():
            if "mailing" in k or "mail address" in k:
                if not record["mailing_address"]:
                    record["mailing_address"] = v

        # Match Property Use
        for k, v in key_values.items():
            if any(term in k for term in ["property use", "use code", "dor code", "land use"]):
                if not record["property_use"]:
                    record["property_use"] = v

        # Match Taxing District
        for k, v in key_values.items():
            if "taxing district" in k or "district" in k:
                if not record["taxing_district"]:
                    record["taxing_district"] = v

        # Match Land Size
        for k, v in key_values.items():
            if any(term in k for term in ["land size", "acres", "acreage", "lot size"]):
                if not record["land_size"]:
                    record["land_size"] = v

        # Match Exemptions
        for k, v in key_values.items():
            if "exemption" in k:
                if not record["exemptions"]:
                    record["exemptions"] = v

        # Match Map Number
        for k, v in key_values.items():
            if "map number" in k or "map #" in k:
                if not record["map_number"]:
                    record["map_number"] = v

        # Match Legal Description
        for k, v in key_values.items():
            if "legal description" in k or "short legal" in k or "legal" == k:
                if not record["legal_description"]:
                    record["legal_description"] = v

        # Match Subdivision
        for k, v in key_values.items():
            if "subdivision" in k:
                if not record["subdivision"]:
                    record["subdivision"] = v

        # Match Valuation Values
        for k, v in key_values.items():
            if "just value" in k or "market value" in k or "just / market" in k:
                num = parse_numeric_value(v)
                if num is not None and not record["just_value"]:
                    record["just_value"] = num
            elif "assessed value" in k or "assessed" in k:
                num = parse_numeric_value(v)
                if num is not None and not record["assessed_value"]:
                    record["assessed_value"] = num
            elif "taxable value" in k or "taxable" in k:
                num = parse_numeric_value(v)
                if num is not None and not record["taxable_value"]:
                    record["taxable_value"] = num

        # 4. Extract Multi-row Tables (Sales History, Valuation History)
        for tbl in tables:
            hdr_str = " ".join(tbl["headers"]).lower()
            if any(term in hdr_str for term in ["sale date", "deed", "grantor", "grantee", "book/page", "instrument"]):
                # Sales History Table
                sales: list[dict[str, Any]] = []
                for row in tbl["rows"]:
                    sale_entry = {}
                    for idx, h in enumerate(tbl["headers"]):
                        if idx < len(row):
                            sale_entry[h.lower().replace(" ", "_")] = row[idx]
                    if sale_entry:
                        sales.append(sale_entry)
                record["sales_history"] = sales

            elif any(term in hdr_str for term in ["valuation", "year", "assessed", "just value"]):
                val_history: list[dict[str, Any]] = []
                for row in tbl["rows"]:
                    val_entry = {}
                    for idx, h in enumerate(tbl["headers"]):
                        if idx < len(row):
                            val_entry[h.lower().replace(" ", "_")] = row[idx]
                    if val_entry:
                        val_history.append(val_entry)
                record["valuation_history"] = val_history
                # If top valuation row has values, seed assessed/just value if missing
                if val_history and not record["assessed_value"]:
                    for candidate in val_history:
                        for k, v in candidate.items():
                            if "assessed" in k and not record["assessed_value"]:
                                record["assessed_value"] = parse_numeric_value(v)
                            if "just" in k and not record["just_value"]:
                                record["just_value"] = parse_numeric_value(v)

        # Fallback parcel from query_value if not extracted
        if not record["parcel_number"] and query_value:
            record["parcel_number"] = query_value

        return record


# Global singleton instance
_ocr_service: MistralWebpageOCRService | None = None


def get_mistral_webpage_ocr() -> MistralWebpageOCRService:
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = MistralWebpageOCRService()
    return _ocr_service
