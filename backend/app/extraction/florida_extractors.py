"""HTML/DOM extraction for Florida county property appraiser portals."""

import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.extraction.schemas import ParcelRecord

FLORIDA_SCRAPE_JS = """
() => {
  const data = {};
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();

  const set = (key, val) => {
    const v = clean(val);
    if (key && v && v.length < 1000) data[key.toLowerCase()] = v;
  };

  const byId = (id, key) => {
    const el = document.getElementById(id);
    if (el) set(key || id.replace(/_/g, ' '), el.innerText || el.textContent);
  };

  // Baker PA and similar county PHP detail pages
  byId('main_parcel', 'parcel id');
  byId('owner_name', 'owner name');
  byId('site_address', 'site address');
  byId('mailing1', 'mailing address');
  byId('legal', 'legal description');
  byId('use_cd', 'property use');
  byId('tax_dist', 'taxing district');
  byId('exemptions', 'exemptions');
  byId('mkt', 'market value');
  byId('tax_val_co', 'assessed value');
  byId('land_cm', 'land value');
  byId('bld', 'building value');
  byId('acres', 'land size');

  const csz = document.getElementById('csz');
  if (csz && data['mailing address']) {
    data['mailing address'] = clean(data['mailing address'] + ' ' + (csz.innerText || csz.textContent || ''));
  }

  // Subhead label rows: Owner / Site Address / etc.
  document.querySelectorAll('.subhead, .subHead').forEach((label) => {
    const key = clean(label.innerText || label.textContent).replace(/:$/, '');
    const row = label.closest('.row, tr, li, .mailingadd, div');
    if (!row) return;
    const valueEl = row.querySelector('[id], .namespan, .mailwrapper span:not(.subhead), .citystz, td:last-child, .value');
    if (valueEl && valueEl !== label) {
      set(key, valueEl.innerText || valueEl.textContent);
    }
  });

  // Label / value pairs in tables and definition lists
  document.querySelectorAll('tr, dl, .detail-row, .property-row, mat-row').forEach(row => {
    const cells = row.querySelectorAll('th, td, dt, dd');
    if (cells.length < 2) return;
    const key = clean(cells[0].innerText || cells[0].textContent);
    const val = clean(cells[1].innerText || cells[1].textContent);
    if (key && val && key.length < 80 && val.length < 500 && key !== val) {
      set(key, val);
    }
  });

  // Angular/material style: label above value
  document.querySelectorAll('label, .label, .field-label, strong, b').forEach(label => {
    const key = clean(label.innerText || label.textContent).replace(/:$/, '');
    if (!key || key.length > 80) return;
    const parent = label.closest('div, li, tr, .form-group, .field');
    if (!parent) return;
    const valEl = parent.querySelector('.value, .field-value, span:not(label span), p, div:nth-child(2)');
    const val = valEl ? clean(valEl.innerText || valEl.textContent) : '';
    if (val && val !== key) set(key, val);
  });

  document.querySelectorAll('h1, h2, h3, h4, .card-title, .section-title').forEach(h => {
    const text = clean(h.innerText || h.textContent);
    const parcelHeading = text.match(/Parcel\\s*ID:?\\s*([\\w.-]+)/i);
    if (parcelHeading) set('parcel id', parcelHeading[1]);
    const sib = h.nextElementSibling;
    if (sib) {
      const val = clean(sib.innerText || sib.textContent);
      if (val && val.length < 500) set(text.replace(/:$/, ''), val);
    }
  });

  const urlMatch = location.href.match(/[?&]parcel=([^&#]+)/i)
    || location.href.match(/[?&]folio=([^&#]+)/i)
    || location.href.match(/[?&]pin=([^&#]+)/i);
  if (urlMatch && !data['parcel id']) {
    set('parcel id', decodeURIComponent(urlMatch[1]));
  }

  return data;
}
"""


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _parse_value(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def _parse_currency_value(text: Optional[str]) -> Optional[float]:
    """Parse dollar amounts; ignore bare years like 2026 mistaken for assessed value."""
    if not text:
        return None
    raw = str(text).strip()
    if "$" in raw:
        return _parse_value(raw)
    val = _parse_value(raw)
    if val is None:
        return None
    if 2000 <= val <= 2035 and "." not in raw.replace(",", ""):
        return None
    if val >= 1000:
        return val
    return None


_GARBAGE_ASSESSOR_MARKERS = (
    "search criteria",
    "back to search",
    "property search",
    "subdivision name folio",
    "owner name subdivision",
    "111 nw 1 st suite",
)

MIAMI_DADE_FOLIO_RE = re.compile(r"^\d{2}-\d{4}-\d{3}-\d{4}$")
MIAMI_DADE_FOLIO_FIND_RE = re.compile(r"\b(\d{2}-\d{4}-\d{3}-\d{4})\b")


def extract_valid_miami_dade_folio(*candidates: Optional[str]) -> Optional[str]:
    for raw in candidates:
        if not raw:
            continue
        text = re.sub(r"\s+", " ", str(raw)).strip()
        if MIAMI_DADE_FOLIO_RE.match(text):
            return text
        match = MIAMI_DADE_FOLIO_FIND_RE.search(text)
        if match:
            return match.group(1)
        digits = re.sub(r"\D", "", text)
        if len(digits) == 13:
            from app.config.florida_portals import normalize_florida_parcel

            return normalize_florida_parcel(digits, county="miami-dade")
    return None


def is_valid_miami_dade_legal_desc(text: Optional[str]) -> bool:
    if not text:
        return False
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) < 25:
        return False
    if re.match(r"^(patio|wall|pool|shed|carport)\s*[-–]", cleaned, re.I):
        return False
    if re.search(r"\$\s*[\d,]+\s*$", cleaned) and len(cleaned) < 80:
        return False
    return True


def is_garbage_assessor_text(text: Optional[str]) -> bool:
    if not text:
        return True
    lower = re.sub(r"\s+", " ", text).strip().lower()
    if not lower:
        return True
    if any(marker in lower for marker in _GARBAGE_ASSESSOR_MARKERS):
        return True
    if len(lower) > 350:
        return True
    return False


def is_valid_miami_dade_extraction(parcel: ParcelRecord) -> bool:
    if not parcel.apn or not MIAMI_DADE_FOLIO_RE.match(str(parcel.apn)):
        return False
    if is_garbage_assessor_text(parcel.owner_name):
        return False
    if is_garbage_assessor_text(parcel.property_address):
        return False
    if parcel.property_address and len(parcel.property_address) > 220:
        return False
    return bool(parcel.owner_name or parcel.property_address)


def _pick_field(data: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        val = data.get(key.lower())
        if val:
            return _clean(str(val))
    return None


def _parcel_from_url(source_url: str) -> Optional[str]:
    if not source_url:
        return None
    for pattern in (
        r"[?&]parcel=([^&#]+)",
        r"[?&]folio=([^&#]+)",
        r"[?&]pin=([^&#]+)",
        r"#/?folio/([^/?&#]+)",
    ):
        match = re.search(pattern, source_url, re.I)
        if match:
            return _clean(match.group(1))
    return None


def _extract_labeled_detail_fields(soup: BeautifulSoup) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for el_id, key in (
        ("main_parcel", "parcel id"),
        ("owner_name", "owner name"),
        ("site_address", "site address"),
        ("mailing1", "mailing address"),
        ("legal", "legal description"),
        ("use_cd", "property use"),
        ("tax_dist", "taxing district"),
        ("exemptions", "exemptions"),
        ("mkt", "market value"),
        ("tax_val_co", "assessed value"),
        ("land_cm", "land value"),
        ("bld", "building value"),
        ("acres", "land size"),
    ):
        node = soup.select_one(f"#{el_id}")
        if node:
            data[key] = _clean(node.get_text(" ", strip=True))

    csz = soup.select_one("#csz")
    if csz and data.get("mailing address"):
        data["mailing address"] = _clean(f"{data['mailing address']} {csz.get_text(' ', strip=True)}")

    for label in soup.select(".subhead, .subHead"):
        key = _clean(label.get_text())
        if not key:
            continue
        key = key.rstrip(":").lower()
        row = label.find_parent(class_=re.compile(r"row|mailingadd", re.I)) or label.parent
        if not row:
            continue
        value_parts: list[str] = []
        for span in row.select("span, div"):
            classes = " ".join(span.get("class") or [])
            if span is label or "subhead" in classes.lower():
                continue
            text = _clean(span.get_text(" ", strip=True))
            if text and text.lower() != key:
                value_parts.append(text)
        if value_parts:
            data[key] = _clean(" ".join(value_parts))
    return data


def parcel_record_from_florida_data(data: dict[str, Any], source_url: str = "") -> ParcelRecord:
    apn = _pick_field(
        data,
        "parcel id",
        "parcel",
        "parcel number",
        "parcel #",
        "pin",
        "folio",
        "folio number",
        "property id",
        "tax id",
        "strap",
        "main parcel",
    ) or _parcel_from_url(source_url)
    owner = _pick_field(
        data,
        "owner",
        "owner name",
        "owner(s)",
        "owner names",
        "property owner",
        "owner 1",
    )
    address = _pick_field(
        data,
        "site address",
        "property address",
        "situs address",
        "physical address",
        "location",
        "address",
        "mailing address",
    )
    legal = _pick_field(
        data,
        "legal description",
        "legal",
        "description",
        "subdivision",
    )
    value_text = _pick_field(
        data,
        "just value",
        "market value",
        "assessed value",
        "total assessed value",
        "total value",
        "land value",
        "building value",
        "tax value",
    )

    sales: list[dict[str, Any]] = []
    for key, val in data.items():
        if "sale" in key and val:
            sales.append({"field": key, "value": val})

    raw = dict(data)
    if sales:
        raw["sales_hints"] = sales

    if source_url:
        raw["source_url"] = source_url

    return ParcelRecord(
        apn=apn,
        owner_name=owner,
        legal_desc=legal,
        assessed_value=_parse_value(value_text),
        property_address=address,
        source="assessor",
        raw_json=raw,
    )


FLORIDA_PA_DETAIL_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const data = { sales_history: [], buildings: [], land: [], fields: {} };

  const cellValue = (label) => {
    const target = label.toLowerCase();
    for (const cell of document.querySelectorAll('td, th')) {
      const text = norm(cell.innerText).toLowerCase().replace(/:$/, '').replace(/\\*+$/, '');
      if (text !== target) continue;
      const row = cell.closest('tr');
      if (!row) continue;
      const cells = [...row.querySelectorAll('td, th')];
      const idx = cells.indexOf(cell);
      if (idx >= 0 && cells[idx + 1]) {
        return norm(cells[idx + 1].innerText);
      }
    }
    return '';
  };

  const bodyText = document.body.innerText || '';
  const parcelMatch = bodyText.match(/Parcel:\\s*([0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{5}-[0-9]{3}(?:\\s*\\(\\d+\\))?)/i);
  const parcelIdMatch = bodyText.match(/Parcel ID:\\s*([0-9]{5}-[0-9]-[0-9]{5}(?:\\s*\\(\\d+\\))?)/i);
  data.parcel = (parcelMatch ? norm(parcelMatch[1]) : '')
    || (parcelIdMatch ? norm(parcelIdMatch[1]) : '')
    || cellValue('parcel id')
    || cellValue('parcel');
  data.owner = cellValue('owner');
  data.site = cellValue('site');
  data.description = cellValue('description') || cellValue('description*');
  data.land_area = cellValue('land area');
  data.area = cellValue('area') || data.land_area;
  data.section_township_range = cellValue('s/t/r');
  data.use_code = cellValue('use code');
  data.tax_district = cellValue('tax district');

  const assessedMatches = [...bodyText.matchAll(/Assessed[\\s\\n]+\\$([\\d,]+)/gi)];
  if (assessedMatches.length) {
    data.assessed_value = '$' + assessedMatches[assessedMatches.length - 1][1];
  }
  const justMatches = [...bodyText.matchAll(/Just[\\s\\n]+\\$([\\d,]+)/gi)];
  if (justMatches.length) {
    data.just_value = '$' + justMatches[justMatches.length - 1][1];
  }

  document.querySelectorAll('table').forEach((table) => {
    const rows = [...table.querySelectorAll('tr')];
    if (rows.length < 2) return;
    const headerCells = [...rows[0].querySelectorAll('th, td')].map((c) => norm(c.innerText).toLowerCase());
    if (headerCells[0] === 'sale date' && headerCells[1] === 'sale price') {
      rows.slice(1).forEach((tr) => {
        const cells = [...tr.querySelectorAll('td')].map((c) => norm(c.innerText));
        if (cells.length >= 4 && /^\\d/.test(cells[0])) {
          data.sales_history.push({
            sale_date: cells[0],
            sale_price: cells[1],
            book_page: cells[2],
            deed_type: cells[3],
            vi: cells[4] || '',
            qualification: cells[5] || '',
            rcode: cells[6] || '',
          });
        }
      });
    }
    if (headerCells.includes('year blt') && headerCells.includes('bldg value')) {
      rows.slice(1).forEach((tr) => {
        const cells = [...tr.querySelectorAll('td')].map((c) => norm(c.innerText));
        if (cells.length >= 6 && cells[0].toLowerCase() === 'sketch' && /^\\d{4}$/.test(cells[2])) {
          data.buildings.push({
            sketch: cells[0],
            description: cells[1],
            year_built: cells[2],
            base_sf: cells[3],
            actual_sf: cells[4],
            building_value: cells[5],
          });
        }
      });
    }
    if (headerCells.includes('land value') && headerCells.includes('eff rate')) {
      rows.slice(1).forEach((tr) => {
        const cells = [...tr.querySelectorAll('td')].map((c) => norm(c.innerText));
        if (cells.length >= 5 && /^\\d+$/.test(cells[0]) && /SF|AC/i.test(cells[2])) {
          data.land.push({
            code: cells[0],
            description: cells[1],
            units: cells[2],
            adjustments: cells[3],
            eff_rate: cells[4],
            land_value: cells[5] || '',
          });
        }
      });
    }
    const headerText = norm(rows[0].innerText).toLowerCase();
    if (headerText.includes('certified') && headerText.includes('working')) {
      const comparison = [];
      rows.slice(1).forEach((tr) => {
        const cells = [...tr.querySelectorAll('td, th')].map((c) => norm(c.innerText));
        if (cells.length >= 2 && cells[0]) {
          comparison.push({
            label: cells[0],
            certified: cells[1] || '',
            working: cells[2] || '',
          });
        }
      });
      if (comparison.length) {
        data.assessment_comparison = comparison;
      }
    }
  });

  return data;
}
"""


def _split_florida_pa_owner(owner_block: str) -> tuple[str | None, str | None, str]:
    block = re.sub(r"\s+", " ", owner_block or "").strip()
    if not block:
        return None, None, ""

    addr_match = re.search(r"\b(\d{1,6}\s+[NSEW]?\s*)", block)
    if not addr_match:
        return block, None, block

    names_part = block[: addr_match.start()].strip()
    mailing_address = block[addr_match.start() :].strip()
    tokens = names_part.split()
    owner_name = names_part
    for i in range(1, len(tokens)):
        if i >= 4 and tokens[i] == tokens[0]:
            owner_name = " ".join(tokens[:i])
            break
    else:
        owner_name = " ".join(tokens[:5]) if len(tokens) > 5 else names_part

    return owner_name or None, mailing_address or None, names_part


def _florida_pa_sales_to_chain(sales: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.extraction.assessor_book_page import is_valid_sale_date

    chain: list[dict[str, Any]] = []
    for sale in sales:
        if not isinstance(sale, dict):
            continue
        sale_date = sale.get("sale_date")
        if not is_valid_sale_date(sale_date):
            continue
        chain.append(
            {
                "recording_date": sale_date,
                "sale_price": _parse_value(sale.get("sale_price")),
                "book_page": sale.get("book_page"),
                "document_type": sale.get("deed_type") or "Sale",
                "instrument_number": None,
                "grantor": None,
                "grantee": None,
                "recorder_url": sale.get("recorder_url"),
            }
        )
    return chain


def parcel_record_from_florida_pa_detail(data: dict[str, Any], source_url: str = "") -> ParcelRecord:
    parcel_raw = data.get("parcel") or data.get("fields", {}).get("parcel", "")
    parcel_raw = re.sub(r"\s*\(\d+\)\s*", "", str(parcel_raw)).strip()
    if not re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{5}-\d{3}|\d{5}-\d-\d{5}", parcel_raw):
        compact = re.sub(r"\s+", "-", parcel_raw)
        if re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{5}-\d{3}|\d{5}-\d-\d{5}", compact):
            parcel_raw = compact
        elif re.search(r"\d{11,14}", parcel_raw.replace("-", "")):
            from app.config.florida_portals import normalize_florida_pa_parcel

            parcel_raw = normalize_florida_pa_parcel(parcel_raw)

    owner_block = (data.get("owner") or "").replace("\r\n", "\n")
    owner_lines = [line.strip() for line in owner_block.split("\n") if line.strip()]
    if len(owner_lines) > 1:
        owner_name = owner_lines[0]
        mailing_address = ", ".join(owner_lines[1:])
        all_owners = owner_block
    else:
        owner_name, mailing_address, all_owners = _split_florida_pa_owner(owner_block)

    value_text = data.get("assessed_value") or data.get("just_value")

    raw: dict[str, Any] = {
        "source_url": source_url,
        "platform": "floridapa.com",
        "parcel_detail": data,
        "owners": all_owners,
        "mailing_address": mailing_address,
        "area": data.get("area") or data.get("land_area"),
        "section_township_range": data.get("section_township_range"),
        "use_code": data.get("use_code"),
        "tax_district": data.get("tax_district"),
        "sales_history": data.get("sales_history") or [],
        "building_characteristics": data.get("buildings") or [],
        "land_breakdown": data.get("land") or [],
        "assessment_comparison": data.get("assessment_comparison") or [],
        "assessment_fields": data.get("fields") or {},
        "chain_of_title": _florida_pa_sales_to_chain(data.get("sales_history") or []),
    }

    return ParcelRecord(
        apn=parcel_raw or None,
        owner_name=owner_name,
        legal_desc=data.get("description"),
        assessed_value=_parse_value(value_text),
        property_address=data.get("site"),
        source="assessor",
        raw_json=raw,
    )


def extract_florida_parcel_from_html(html: str, source_url: str = "") -> ParcelRecord:
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, Any] = _extract_labeled_detail_fields(soup)

    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = _clean(cells[0].get_text())
            val = _clean(cells[1].get_text())
            if key and val:
                data[key.lower()] = val

    for label in soup.select("label, .label, .field-label, dt, th, strong"):
        key = _clean(label.get_text())
        if not key:
            continue
        val_el = (
            label.find_next(["dd", "td", "span", "div", "p"])
            or label.find_next_sibling()
        )
        if val_el:
            val = _clean(val_el.get_text())
            if val and val.lower() != key.lower().rstrip(":"):
                data[key.lower().rstrip(":")] = val

    # Orange OCPA card sections
    for card in soup.select(".card, .panel, .property-card, mat-card"):
        title = card.select_one("h1, h2, h3, h4, .card-title, .panel-title")
        if title:
            key = _clean(title.get_text())
            body = _clean(card.get_text())
            if key and body:
                data[key.lower()] = body

    return parcel_record_from_florida_data(data, source_url)


MIAMI_DADE_DETAIL_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const FOLIO_RE = /\\b(\\d{2}-\\d{4}-\\d{3}-\\d{4})\\b/;
  const data = {
    fields: {},
    sections: {},
    sales_history: [],
    assessment_information: [],
    benefits_information: [],
    taxable_value_information: [],
    land_information: {},
    building_information: {},
    extra_features: [],
    additional_information: {},
  };
  const bodyText = document.body.innerText || '';

  const isGarbage = (text) => /search criteria|back to search|property search|111 nw 1 st|^search:?$/i.test(text || '');

  const addField = (key, val) => {
    if (!key || !val || isGarbage(key) || isGarbage(val)) return;
    const k = norm(key).toLowerCase().replace(/[:#*]/g, '').trim();
    const v = norm(val);
    if (!v || v.length > 500 || k === v.toLowerCase()) return;
    data.fields[k] = v;
  };

  const sliceSection = (startPattern, endPattern) => {
    const start = bodyText.search(startPattern);
    if (start < 0) return '';
    const chunk = bodyText.slice(start);
    const end = chunk.search(endPattern);
    return end > 0 ? chunk.slice(0, end) : chunk.slice(0, 4000);
  };

  const propertySection = sliceSection(/PROPERTY INFORMATION/i, /Featured Online Tools|ASSESSMENT INFORMATION/i);
  const propertyLines = propertySection.split('\\n').map(norm).filter(Boolean);
  const propertyMap = {
    'folio #': 'folio',
    folio: 'folio',
    'sub-division': 'subdivision',
    subdivision: 'subdivision',
    'property address': 'property_address',
    owner: 'owner',
    'mailing address': 'mailing_address',
    'pa primary zone': 'pa_primary_zone',
    'pa secondary zone': 'pa_secondary_zone',
    'primary land use': 'primary_land_use',
    'lot size': 'lot_size',
    floors: 'floors',
    'living units': 'living_units',
    'actual area': 'actual_area',
    'living area': 'living_area',
    'adjusted area': 'adjusted_area',
    'year built': 'year_built',
  };

  for (let i = 0; i < propertyLines.length; i++) {
    const line = propertyLines[i];
    const key = line.toLowerCase().replace(/[:#]/g, '').trim();
    const folioInline = line.match(/folio\\s*#?\\s*:?\\s*(\\d{2}-\\d{4}-\\d{3}-\\d{4})/i);
    if (folioInline) {
      data.folio = folioInline[1];
      data.fields.folio = folioInline[1];
      continue;
    }
    const isNextPropertyKey = (candidate) => {
      const k = candidate.toLowerCase().replace(/[:#]/g, '').trim();
      return (
        Boolean(propertyMap[k])
        || /^(\\d+)\\s*\\/\\s*(\\d+)\\s*\\/\\s*(\\d+)$/.test(candidate)
        || /^[\\d,]+\\s*Sq\\.?\\s*Ft/i.test(candidate)
        || /^Year Built$/i.test(candidate)
      );
    };
    if (propertyMap[key] && i + 1 < propertyLines.length) {
      const values = [];
      let j = i + 1;
      while (j < propertyLines.length && !isNextPropertyKey(propertyLines[j])) {
        if (!isGarbage(propertyLines[j])) values.push(propertyLines[j]);
        j += 1;
      }
      const val = values.join(' ').trim();
      if (val && val.length < 400) {
        data[propertyMap[key]] = val;
        data.fields[propertyMap[key]] = val;
        i = Math.max(i, j - 1);
      }
      continue;
    }
    const bedMatch = line.match(/^(\\d+)\\s*\\/\\s*(\\d+)\\s*\\/\\s*(\\d+)$/);
    if (bedMatch) {
      data.bedrooms = bedMatch[1];
      data.bathrooms = bedMatch[2];
      data.half_baths = bedMatch[3];
    }
  }

  if (!data.folio) {
    const folioMatch = propertySection.match(FOLIO_RE) || bodyText.match(FOLIO_RE);
    if (folioMatch) {
      data.folio = folioMatch[1];
      data.fields.folio = folioMatch[1];
    }
  }

  const SECTION_MARKERS = [
    'PROPERTY INFORMATION',
    'Featured Online Tools',
    'ASSESSMENT INFORMATION',
    'BENEFITS INFORMATION',
    'TAXABLE VALUE INFORMATION',
    'FULL LEGAL DESCRIPTION',
    'SALES INFORMATION',
    'LAND INFORMATION',
    'BUILDING INFORMATION',
    'EXTRA FEATURES',
    'ADDITIONAL INFORMATION',
  ];

  const splitBodyIntoSections = (text) => {
    const sections = {};
    const upper = text.toUpperCase();
    const positions = SECTION_MARKERS
      .map((name) => ({ name, pos: upper.indexOf(name.toUpperCase()) }))
      .filter((x) => x.pos >= 0)
      .sort((a, b) => a.pos - b.pos);
    for (let i = 0; i < positions.length; i++) {
      const start = positions[i].pos + positions[i].name.length;
      const end = i + 1 < positions.length ? positions[i + 1].pos : text.length;
      sections[positions[i].name.toLowerCase()] = text.slice(start, end);
    }
    return sections;
  };

  const sectionBlocks = splitBodyIntoSections(bodyText);

  const parseYearsFromText = (text) => {
    const found = text.match(/\\b(20\\d{2})\\b/g);
    if (!found) return ['2026', '2025', '2024'];
    return [...new Set(found)].sort((a, b) => Number(b) - Number(a));
  };

  const extractDomTable = (title) => {
    const el = findSectionElement(title);
    if (!el) return null;
    let container = el.parentElement;
    for (let i = 0; i < 6 && container; i++) {
      const table = container.querySelector('table');
      if (table) {
        const grid = [...table.querySelectorAll('tr')]
          .map((tr) => [...tr.querySelectorAll('th, td')].map((c) => norm(c.innerText)))
          .filter((r) => r.some(Boolean));
        if (grid.length >= 2) {
          return { headers: grid[0], rows: grid.slice(1) };
        }
      }
      container = container.parentElement;
    }
    return null;
  };

  const extractSalesDomTable = (title) => {
    const el = findSectionElement(title);
    if (!el) return null;
    let container = el.parentElement;
    for (let i = 0; i < 6 && container; i++) {
      const table = container.querySelector('table');
      if (table) {
        const grid = [...table.querySelectorAll('tr')]
          .map((tr) => [...tr.querySelectorAll('th, td')].map((c) => {
            const link = c.querySelector('a[href]');
            return {
              text: norm(c.innerText),
              href: link && link.href ? link.href : null,
            };
          }))
          .filter((r) => r.some((cell) => cell.text));
        if (grid.length >= 2) {
          return {
            headers: grid[0].map((c) => c.text),
            rows: grid.slice(1).map((r) => r.map((c) => c.text)),
            row_links: grid.slice(1).map((r) => r.map((c) => c.href)),
          };
        }
      }
      container = container.parentElement;
    }
    return null;
  };

  const findSectionElement = (title) => {
    const target = title.toUpperCase();
    for (const el of document.querySelectorAll('div, span, h2, h3, h4, th, label, p')) {
      const t = norm(el.innerText);
      if (t.toUpperCase() === target && t.length < 80) return el;
    }
    return null;
  };

  const parseAssessmentTable = (text) => {
    const years = parseYearsFromText(text);
    const headers = ['', ...years];
    const lines = text.split('\\n').map(norm).filter((l) => l && l !== '(i)');
    const rows = [];
    const metrics = ['Land Value', 'Building Value', 'Extra Feature Value', 'Market Value', 'Assessed Value'];

    for (const metric of metrics) {
      for (const line of lines) {
        if (line.toLowerCase().startsWith(metric.toLowerCase()) && line.includes('$')) {
          const vals = [...line.matchAll(/\\$[\\d,]+/g)].map((x) => x[0]).slice(0, years.length);
          if (vals.length) {
            rows.push([metric, ...vals]);
            break;
          }
        }
      }
      if (rows.some((r) => r[0] === metric)) continue;
      const idx = lines.findIndex((l) => l.toLowerCase() === metric.toLowerCase());
      if (idx >= 0) {
        const vals = [];
        for (let j = idx + 1; j < lines.length && vals.length < years.length; j++) {
          if (/^\\$[\\d,]+$/.test(lines[j])) vals.push(lines[j]);
          else if (metrics.some((m2) => lines[j].toLowerCase().startsWith(m2.toLowerCase()))) break;
          else if (/^(COUNTY|SCHOOL|BENEFITS|TAXABLE)/i.test(lines[j])) break;
        }
        if (vals.length) rows.push([metric, ...vals]);
      }
    }
    return { headers, rows };
  };

  const parseBenefitsTable = (text) => {
    const years = parseYearsFromText(text);
    const headers = ['Benefit', 'Type', ...years];
    const lines = text.split('\\n').map(norm).filter((l) => l && l !== '(i)');
    const rows = [];
    const known = [
      ['Save Our Homes Cap', 'Assessment Reduction'],
      ['Homestead', 'Exemption'],
      ['Second Homestead', 'Exemption'],
      ['Senior Homestead', 'Exemption'],
      ['Widow', 'Exemption'],
    ];

    for (const [benefit, defaultType] of known) {
      const idx = lines.findIndex((l) => l.toLowerCase().includes(benefit.toLowerCase()));
      if (idx < 0) continue;
      let typeVal = defaultType;
      let start = idx + 1;
      if (lines[start] && /reduction|exemption/i.test(lines[start]) && !/^\\$/.test(lines[start])) {
        typeVal = lines[start];
        start += 1;
      }
      const vals = [];
      for (let j = start; j < lines.length && vals.length < years.length; j++) {
        if (/^\\$[\\d,]+$/.test(lines[j])) vals.push(lines[j]);
        else if (known.some(([b]) => lines[j].toLowerCase().includes(b.toLowerCase()))) break;
      }
      if (vals.length) rows.push([benefit, typeVal, ...vals]);
    }
    return { headers, rows };
  };

  const parseTaxableTable = (text) => {
    const years = parseYearsFromText(text);
    const headers = ['', ...years];
    const lines = text.split('\\n').map(norm).filter(Boolean);
    const rows = [];
    let jurisdiction = '';

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^(COUNTY|SCHOOL BOARD|CITY|REGIONAL)$/i.test(line)) {
        jurisdiction = line;
        continue;
      }
      if (!jurisdiction) continue;
      if (/^exemption value$/i.test(line)) {
        const vals = [];
        for (let j = i + 1; j < lines.length && vals.length < years.length; j++) {
          if (/^\\$[\\d,]+$/.test(lines[j])) vals.push(lines[j]);
          else break;
        }
        if (vals.length) rows.push([`${jurisdiction} — Exemption Value`, ...vals]);
        continue;
      }
      if (/^taxable value$/i.test(line)) {
        const vals = [];
        for (let j = i + 1; j < lines.length && vals.length < years.length; j++) {
          if (/^\\$[\\d,]+$/.test(lines[j])) vals.push(lines[j]);
          else break;
        }
        if (vals.length) rows.push([`${jurisdiction} — Taxable Value`, ...vals]);
        continue;
      }
      const dollars = [...line.matchAll(/\\$[\\d,]+/g)].map((x) => x[0]);
      if (!dollars.length) continue;
      let label = line.replace(jurisdiction, '').split('$')[0].trim();
      if (/exemption value/i.test(line)) label = `${jurisdiction} — Exemption Value`;
      else if (/taxable value/i.test(line)) label = `${jurisdiction} — Taxable Value`;
      else if (label) label = `${jurisdiction} — ${label}`;
      else continue;
      rows.push([label, ...dollars.slice(0, years.length)]);
    }
    return { headers, rows };
  };

  const parseSalesTableText = (text) => {
    const headers = ['Previous Sale', 'Price', 'OR Book-Page', 'Qualification Description', 'Previous Owner 1'];
    const lines = text.split('\\n').map(norm).filter(Boolean);
    const rows = [];
    for (let i = 0; i < lines.length; i++) {
      if (!/^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/.test(lines[i])) continue;
      const row = [lines[i], '', '', '', ''];
      for (let j = i + 1; j < Math.min(i + 8, lines.length); j++) {
        if (/^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/.test(lines[j])) break;
        if (!row[1] && /^\\$/.test(lines[j])) row[1] = lines[j];
        else if (!row[2] && /\\d+\\s*[\\/-]\\s*\\d+/.test(lines[j])) row[2] = lines[j];
        else if (!row[3] && lines[j].length > 8 && !/^\\$/.test(lines[j]) && !/^[A-Z ]{2,15}$/.test(lines[j])) row[3] = lines[j];
        else if (!row[4] && /^[A-Z]/.test(lines[j]) && lines[j].length > 4) row[4] = lines[j];
      }
      rows.push(row);
    }
    return { headers, rows };
  };

  const parseLandBuildingTable = (text) => {
    const lines = text.split('\\n').map(norm).filter((l) => l && !/information$/i.test(l));
    const rows = [];
    for (let i = 0; i < lines.length - 1; i++) {
      const label = lines[i];
      const value = lines[i + 1];
      if (label.length > 45 || /^\\$/.test(label) || /^\\d{1,2}\\/\\d/.test(label)) continue;
      if (!value || value.length > 150 || /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/.test(value)) continue;
      rows.push([label, value]);
      i += 1;
    }
    return { headers: ['', ''], rows };
  };

  const parseExtraFeaturesTable = (text) => {
    const headers = ['', 'Year Built', 'Units', 'Calc Value'];
    const lines = text.split('\\n').map(norm).filter(Boolean);
    const rows = [];
    for (let i = 0; i < lines.length; i++) {
      if (!/^(patio|wall|pool|shed|carport|fence|roof|driveway|utility)/i.test(lines[i])) continue;
      rows.push([
        lines[i],
        /^\\d{4}$/.test(lines[i + 1] || '') ? lines[i + 1] : '',
        /^[\\d,]+$/.test(lines[i + 2] || '') ? lines[i + 2] : '',
        /^\\$/.test(lines[i + 3] || '') ? lines[i + 3] : '',
      ]);
    }
    return { headers, rows };
  };

  const parseLegalSection = (text) => {
    return text.split('\\n').map(norm).filter((l) => (
      l
      && !/full legal description/i.test(l)
      && !/sales information/i.test(l)
      && !/sales transaction history/i.test(l)
      && !/previous sale/i.test(l)
    )).join('\\n').slice(0, 2000);
  };

  const pickTable = (title, text, textParser) => extractDomTable(title) || textParser(text);

  data.assessment_information_table = pickTable(
    'ASSESSMENT INFORMATION',
    sectionBlocks['assessment information'] || '',
    parseAssessmentTable,
  );
  data.benefits_information_table = pickTable(
    'BENEFITS INFORMATION',
    sectionBlocks['benefits information'] || '',
    parseBenefitsTable,
  );
  data.taxable_value_information_table = pickTable(
    'TAXABLE VALUE INFORMATION',
    sectionBlocks['taxable value information'] || '',
    parseTaxableTable,
  );
  data.sales_information_table = extractSalesDomTable('SALES INFORMATION') || pickTable(
    'SALES INFORMATION',
    sectionBlocks['sales information'] || '',
    parseSalesTableText,
  );
  data.land_information_table = pickTable(
    'LAND INFORMATION',
    sectionBlocks['land information'] || '',
    parseLandBuildingTable,
  );
  data.building_information_table = pickTable(
    'BUILDING INFORMATION',
    sectionBlocks['building information'] || '',
    parseLandBuildingTable,
  );
  data.extra_features_table = pickTable(
    'EXTRA FEATURES',
    sectionBlocks['extra features'] || '',
    parseExtraFeaturesTable,
  );

  data.sales_history = (data.sales_information_table.rows || [])
    .map((row, idx) => {
      const links = data.sales_information_table.row_links || [];
      return {
        sale_date: row[0] || '',
        sale_price: row[1] || '',
        book_page: row[2] || '',
        qualification: row[3] || '',
        previous_owner: row[4] || '',
        recorder_url: (links[idx] && links[idx][2]) || null,
        deed_type: 'Sale',
      };
    })
    .filter((entry) => /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/.test(entry.sale_date));

  const legalText = sectionBlocks['full legal description'] || '';
  if (legalText) {
    data.full_legal_description = parseLegalSection(legalText);
    data.legal_description = data.full_legal_description;
    if (data.full_legal_description) data.fields['full legal description'] = data.full_legal_description;
  }

  const additionalText = sectionBlocks['additional information'] || '';
  if (additionalText) {
    data.additional_information = { body_text: additionalText.slice(0, 4000) };
    data.sections['additional information'] = additionalText.slice(0, 3000);
  }

  for (const [name, text] of Object.entries(sectionBlocks)) {
    if (text && name !== 'property information' && name !== 'featured online tools') {
      data.sections[name] = text.slice(0, 3000);
    }
  }

  const assessedRow = (data.assessment_information_table?.rows || []).find((row) => /assessed value/i.test(row[0] || ''));
  if (assessedRow) {
    const yearVal = assessedRow[1] || assessedRow[2] || assessedRow[3];
    if (yearVal) data.assessed_value = yearVal.startsWith('$') ? yearVal : `$${yearVal}`;
  }
  if (!data.assessed_value) {
    const assessedMatches = [...bodyText.matchAll(/Assessed Value\\s+\\$?([\\d,]+)/gi)];
    if (assessedMatches.length) data.assessed_value = `$${assessedMatches[assessedMatches.length - 1][1]}`;
  }

  const marketRow = (data.assessment_information_table?.rows || []).find((row) => /market value/i.test(row[0] || ''));
  if (marketRow) {
    data.market_value = marketRow[1] || marketRow[2] || marketRow[3] || '';
  }

  if (!data.property_address) {
    const m = bodyText.match(/Property Address\\s*\\n\\s*([^\\n]{5,100})/i);
    if (m) data.property_address = norm(m[1]);
  }
  if (!data.owner) {
    const m = bodyText.match(/Owner\\s*\\n\\s*([^\\n]{3,120})/i);
    if (m && !isGarbage(m[1])) data.owner = norm(m[1]);
  }

  data.site = data.property_address;
  return data;
}
"""


def parcel_record_from_miami_dade_detail(data: dict[str, Any], source_url: str = "") -> ParcelRecord:
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}

    folio = extract_valid_miami_dade_folio(
        data.get("folio"),
        fields.get("folio"),
        fields.get("folio #"),
    )

    owner_block = (data.get("owner") or fields.get("owner") or "").replace("\r\n", "\n")
    owner_lines = [line.strip() for line in owner_block.split("\n") if line.strip()]
    if owner_lines:
        owner_name = " ".join(owner_lines)
    else:
        owner_name = owner_block or None
    if is_garbage_assessor_text(owner_name):
        owner_name = None

    value_text = data.get("assessed_value") or fields.get("assessed_value")
    site = data.get("property_address") or data.get("site") or fields.get("property address")
    if is_garbage_assessor_text(site):
        site = None

    description = (
        data.get("full_legal_description")
        or data.get("legal_description")
        or fields.get("full legal description")
    )
    if description:
        description = re.sub(
            r"\s*(Sales Transaction History|Previous Sale|SALES INFORMATION).*$",
            "",
            str(description),
            flags=re.I | re.M,
        ).strip()
    if not is_valid_miami_dade_legal_desc(description):
        description = data.get("subdivision") or fields.get("sub-division") or fields.get("subdivision")

    primary_land_use = data.get("primary_land_use") or fields.get("primary land use") or ""
    if primary_land_use:
        bed_in_use = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", primary_land_use)
        if bed_in_use:
            primary_land_use = primary_land_use[: bed_in_use.start()].strip(" :")
            if not data.get("bedrooms"):
                data["bedrooms"] = bed_in_use.group(1)
                data["bathrooms"] = bed_in_use.group(2)
                data["half_baths"] = bed_in_use.group(3)

    sales = data.get("sales_history") or []
    mailing_address = data.get("mailing_address") or fields.get("mailing address")

    raw: dict[str, Any] = {
        "source_url": source_url,
        "platform": "miamidadepa.gov",
        "parcel_detail": data,
        "fields": fields,
        "sections": data.get("sections") or {},
        "folio": folio,
        "owner": owner_name,
        "mailing_address": mailing_address,
        "subdivision": data.get("subdivision") or fields.get("sub-division") or fields.get("subdivision"),
        "pa_primary_zone": data.get("pa_primary_zone") or fields.get("pa primary zone"),
        "pa_secondary_zone": data.get("pa_secondary_zone") or fields.get("pa secondary zone"),
        "primary_land_use": primary_land_use or None,
        "lot_size": data.get("lot_size") or fields.get("lot size"),
        "actual_area": data.get("actual_area") or fields.get("actual area"),
        "adjusted_area": data.get("adjusted_area") or fields.get("adjusted area"),
        "floors": data.get("floors") or fields.get("floors"),
        "living_units": data.get("living_units") or fields.get("living units"),
        "bedrooms": data.get("bedrooms"),
        "bathrooms": data.get("bathrooms"),
        "half_baths": data.get("half_baths"),
        "living_area": data.get("living_area") or fields.get("living area"),
        "year_built": data.get("year_built") or fields.get("year built"),
        "market_value": data.get("market_value"),
        "assessed_value_text": value_text,
        "assessment_information_table": data.get("assessment_information_table") or {"headers": [], "rows": []},
        "benefits_information_table": data.get("benefits_information_table") or {"headers": [], "rows": []},
        "taxable_value_information_table": data.get("taxable_value_information_table") or {"headers": [], "rows": []},
        "sales_information_table": data.get("sales_information_table") or {"headers": [], "rows": []},
        "land_information_table": data.get("land_information_table") or {"headers": [], "rows": []},
        "building_information_table": data.get("building_information_table") or {"headers": [], "rows": []},
        "extra_features_table": data.get("extra_features_table") or {"headers": [], "rows": []},
        "additional_information": data.get("additional_information") or {},
        "full_legal_description": data.get("full_legal_description") or description,
        "sales_history": sales,
        "chain_of_title": _florida_pa_sales_to_chain(sales),
    }

    return ParcelRecord(
        apn=folio,
        owner_name=owner_name,
        legal_desc=description,
        assessed_value=_parse_currency_value(value_text),
        property_address=site,
        source="assessor",
        raw_json=raw,
    )


def normalize_miami_dade_folio(folio: str) -> str:
    from app.config.florida_portals import normalize_florida_parcel

    return normalize_florida_parcel(folio, county="miami-dade")


# ---------------------------------------------------------------------------
# Collier County Multi-Section Parser
# ---------------------------------------------------------------------------

def _parse_collier_sections_data(
    extracted_sections: dict[str, Any],
    url: str,
    query_value: str = "",
) -> ParcelRecord:
    summary_sec = extracted_sections.get("Summary") or {}
    key_values = summary_sec.get("key_values") or {}
    tables = summary_sec.get("tables") or []

    parcel_id = key_values.get("Parcel ID") or ""
    site_location = key_values.get("Site Location") or ""
    if not site_location:
        for k, v in key_values.items():
            if "site location" in k.lower():
                site_location = v
                break

    owner_name = ""
    owner_lines: list[str] = []
    legal_description = ""
    subdivision = ""
    use_code = ""
    millage_area = ""
    municipality = ""
    strap_no = ""
    map_no = ""
    section = ""
    township = ""
    range_val = ""
    acres: Optional[float] = None
    just_value: Optional[float] = None
    assessed_value: Optional[float] = None
    sales_history: list[dict[str, str]] = []

    # Parse tables in Summary
    for tbl in tables:
        for row in tbl:
            row_str = " ".join(row)
            if "Parcel ID" in row_str and not parcel_id:
                for idx, cell in enumerate(row):
                    if "Parcel ID" in cell and idx + 1 < len(row):
                        parcel_id = row[idx + 1]
            if "Name / Address" in row_str and len(row) >= 2:
                owner_name = row[1]
            elif "Legal" in row_str and len(row) >= 2:
                legal_description = row[1]
            elif "Sub./Condo" in row_str and len(row) >= 2:
                subdivision = row[1]
            elif "Use Code" in row_str:
                for idx, cell in enumerate(row):
                    if "Use Code" in cell and idx + 1 < len(row):
                        use_code = row[idx + 1]
            elif "Millage Area" in row_str:
                for idx, cell in enumerate(row):
                    if "Millage Area" in cell and idx + 1 < len(row):
                        millage_area = row[idx + 1]
            elif "Municipality" in row_str:
                for idx, cell in enumerate(row):
                    if "Municipality" in cell and idx + 1 < len(row):
                        municipality = row[idx + 1]
            elif "Strap No." in row_str:
                for idx, cell in enumerate(row):
                    if "Strap No." in cell and idx + 1 < len(row):
                        strap_no = row[idx + 1]
            elif "Map No." in row_str:
                for idx, cell in enumerate(row):
                    if "Map No." in cell and idx + 1 < len(row):
                        map_no = row[idx + 1]
            elif "Section" in row_str:
                for idx, cell in enumerate(row):
                    if cell == "Section" and idx + 1 < len(row):
                        section = row[idx + 1]
            elif "Township" in row_str:
                for idx, cell in enumerate(row):
                    if cell == "Township" and idx + 1 < len(row):
                        township = row[idx + 1]
            elif "Range" in row_str:
                for idx, cell in enumerate(row):
                    if cell == "Range" and idx + 1 < len(row):
                        range_val = row[idx + 1]
            elif "Acres" in row_str:
                for idx, cell in enumerate(row):
                    if "Acres" in cell and idx + 1 < len(row):
                        try:
                            acres = float(re.sub(r"[^\d.]", "", row[idx + 1]))
                        except (ValueError, TypeError):
                            pass
            elif len(row) == 5 and re.match(r"\d{2}/\d{2}/\d{2}", row[0]):
                sales_history.append({
                    "date": row[0],
                    "book_page": row[1],
                    "vacant_improved": row[2],
                    "qualified": row[3],
                    "amount": row[4],
                })
            elif "Total Just" in row_str:
                for cell in row:
                    if "$" in cell:
                        try:
                            just_value = float(re.sub(r"[^\d.]", "", cell))
                        except (ValueError, TypeError):
                            pass
            elif "(=) Assessed" in row_str or "Assessed" in row_str:
                for cell in row:
                    if "$" in cell:
                        try:
                            assessed_value = float(re.sub(r"[^\d.]", "", cell))
                        except (ValueError, TypeError):
                            pass

    # Detail section
    detail_sec = extracted_sections.get("Detail") or {}
    land_lines: list[dict[str, str]] = []
    permits: list[dict[str, str]] = []
    year_built: Optional[str] = None
    for tbl in detail_sec.get("tables") or []:
        for row in tbl:
            row_str = " ".join(row)
            if len(row) == 2 and row[0] not in ("Land", "Calculation Method") and any(ch.isdigit() for ch in row[1]):
                land_lines.append({"calculation_method": row[0], "units": row[1]})
            elif len(row) >= 5 and re.match(r"\d{2}/\d{2}/\d{2}", row[0]) and "Permit" not in row[0]:
                permits.append({
                    "issued_date": row[0],
                    "issuer": row[1],
                    "permit_number": row[2],
                    "completion_date": row[3],
                    "type": row[4],
                })
            elif len(row) >= 4 and re.match(r"^\d{4}$", row[0]):
                year_built = row[0]

    # Tax Roll section
    tax_roll_sec = extracted_sections.get("Tax Roll") or {}
    ad_valorem: list[dict[str, str]] = []
    total_tax: Optional[str] = None
    for tbl in tax_roll_sec.get("tables") or []:
        for row in tbl:
            row_str = " ".join(row)
            if len(row) == 5 and any(t in row[1] for t in ("County", "School", "Other", "Municipal")):
                ad_valorem.append({
                    "taxing_authority": row[0],
                    "taxable_type": row[1],
                    "taxable_value": row[2],
                    "millage": row[3],
                    "tax": row[4],
                })
            elif "TOTAL TAX" in row_str:
                for cell in row:
                    if "$" in cell:
                        total_tax = cell

    final_apn = parcel_id or query_value or ""
    raw_json = {
        "source": "assessor",
        "county": "collier",
        "portal_url": url,
        "strap_no": strap_no,
        "map_no": map_no,
        "section": section,
        "township": township,
        "range": range_val,
        "acres": acres,
        "year_built": year_built,
        "subdivision": subdivision,
        "use_code": use_code,
        "millage_area": millage_area,
        "municipality": municipality,
        "sales_history": sales_history,
        "land_lines": land_lines,
        "permits": permits,
        "ad_valorem_taxes": ad_valorem,
        "total_tax": total_tax,
        "sections": extracted_sections,
    }

    return ParcelRecord(
        apn=final_apn or None,
        owner_name=owner_name or None,
        property_address=site_location or None,
        legal_description=legal_description or None,
        assessed_value=assessed_value,
        source="assessor",
        raw_json=raw_json,
    )

parse_collier_sections_data = _parse_collier_sections_data


# ---------------------------------------------------------------------------
# Dynamic Full-Page Extraction for any County Assessor Detail Page
# ---------------------------------------------------------------------------

DYNAMIC_FULL_PAGE_EXTRACTION_JS = """
() => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  
  // 1. Collect Key-Value pairs across the entire page
  const keyValues = {};
  const addKV = (rawKey, rawVal) => {
    const k = norm(rawKey).replace(/:$/, '').trim();
    const v = norm(rawVal);
    if (!k || !v || k.length > 80 || v.length > 1000 || k.toLowerCase() === v.toLowerCase()) return;
    if (v === 'OR' || v === 'AND' || v === 'NOT') return; // ignore search operator forms
    const lowerK = k.toLowerCase();
    if (!keyValues[lowerK]) {
      keyValues[lowerK] = v;
    }
  };

  // Inspect dynamic framework data-bind / attribute bindings (Knockout, Angular, Vue, React)
  document.querySelectorAll('[data-bind], [ng-bind]').forEach(el => {
    if (el.closest('th, thead, button, nav, .navbar, footer')) return;
    const bind = (el.getAttribute('data-bind') || el.getAttribute('ng-bind') || '').toLowerCase();
    if (bind.includes('sort') || bind.includes('click') || bind.includes('options')) return;
    const val = norm(el.innerText || el.textContent);
    if (!val || val.length > 500 || val.includes('\u2195')) return;

    if ((bind.includes('publicowner') || bind.includes('owner')) && !keyValues['owner name']) {
      addKV('owner name', val);
    } else if ((bind.includes('siteaddress') || bind.includes('site_address') || bind.includes('propertyaddress')) && !keyValues['site address']) {
      addKV('site address', val);
    } else if (bind.includes('mailingaddress') && !keyValues['mailing address']) {
      addKV('mailing address', val);
    } else if ((bind.includes('displayfolio') || (bind.includes('folio') && /\\d/.test(val))) && !keyValues['folio']) {
      addKV('folio', val);
    } else if (bind.includes('pin') && /\\d/.test(val) && !keyValues['pin']) {
      addKV('pin', val);
    } else if (bind.includes('legal') && !keyValues['legal description']) {
      addKV('legal description', val);
    }
  });

  // Traverse table rows: 2-column or 4-column key-value pairs
  document.querySelectorAll('tr').forEach(tr => {
    if (tr.closest('thead') || tr.querySelector('th:first-child')) {
      const ths = Array.from(tr.querySelectorAll('th, td')).map(c => norm(c.innerText || c.textContent));
      if (ths.length === 2 && ths[0] && ths[1]) addKV(ths[0], ths[1]);
      return;
    }
    const cells = Array.from(tr.querySelectorAll('th, td')).map(c => norm(c.innerText || c.textContent));
    if (cells.length === 2 && cells[0] && cells[1]) {
      addKV(cells[0], cells[1]);
    } else if (cells.length === 4) {
      if (cells[0] && cells[1]) addKV(cells[0], cells[1]);
      if (cells[2] && cells[3]) addKV(cells[2], cells[3]);
    }
  });

  // Extract values from valueSummary / assessment overview tables
  document.querySelectorAll('table').forEach(tbl => {
    const rows = Array.from(tbl.querySelectorAll('tr'));
    if (rows.length < 2) return;
    const headerCells = Array.from(rows[0].querySelectorAll('th, td')).map(c => norm(c.innerText || c.textContent).toLowerCase());
    const headerStr = headerCells.join(' | ');
    if (headerStr.includes('assessed') || headerStr.includes('market') || headerStr.includes('taxable')) {
      const firstDataRow = rows[1];
      const cells = Array.from(firstDataRow.querySelectorAll('th, td')).map(c => norm(c.innerText || c.textContent));
      headerCells.forEach((h, idx) => {
        if (cells[idx] && (h.includes('assessed') || h.includes('market') || h.includes('just') || h.includes('taxable'))) {
          addKV(h, cells[idx]);
        }
      });
    }
  });

  // Definition lists dl/dt/dd
  document.querySelectorAll('dl').forEach(dl => {
    let currentKey = null;
    Array.from(dl.children).forEach(el => {
      const tag = el.tagName.toLowerCase();
      if (tag === 'dt') currentKey = norm(el.innerText || el.textContent);
      else if (tag === 'dd' && currentKey) {
        addKV(currentKey, norm(el.innerText || el.textContent));
        currentKey = null;
      }
    });
  });

  // Labeled elements (.subhead, .clsLabel, label, strong, b, .field-label)
  document.querySelectorAll('.subhead, .subHead, .clsLabel, label, .field-label').forEach(lbl => {
    if (lbl.closest('nav, .navbar, footer')) return;
    const k = norm(lbl.innerText || lbl.textContent);
    let v = '';
    const next = lbl.nextElementSibling;
    if (next && (next.matches('span, div, td, dd, p') || next.className.includes('value'))) {
      v = norm(next.innerText || next.textContent);
    } else {
      const parent = lbl.closest('tr, .row, li, .form-group, .field, div');
      if (parent) {
        const valEl = parent.querySelector('.value, span:not(.subhead):not(.clsLabel), td:last-child');
        if (valEl && valEl !== lbl) {
          v = norm(valEl.innerText || valEl.textContent);
        }
      }
    }
    if (k && v && v !== 'OR' && v !== 'AND') addKV(k, v);
  });

  // Explicit IDs common in county portals:
  const ids = [
    ['main_parcel', 'parcel id'],
    ['owner_name', 'owner name'],
    ['site_address', 'site address'],
    ['mailing1', 'mailing address'],
    ['mailing2', 'mailing address 2'],
    ['csz', 'city state zip'],
    ['legal', 'legal description'],
    ['use_cd', 'property use'],
    ['tax_dist', 'taxing district'],
    ['exemptions', 'exemptions'],
    ['mkt', 'market value'],
    ['tax_val_co', 'assessed value'],
    ['land_cm', 'land value'],
    ['bld', 'building value'],
    ['acres', 'land size'],
    ['map_no', 'map number'],
  ];
  ids.forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (el) {
      const val = norm(el.innerText || el.textContent);
      if (val) addKV(key, val);
    }
  });

  // Extract from Heading text like "Parcel ID: 282S22021600001250"
  document.querySelectorAll('h1, h2, h3, h4, h5, p, span, div').forEach(el => {
    if (el.closest('nav, .navbar, footer')) return;
    const txt = norm(el.innerText || el.textContent);
    const m = txt.match(/PARCEL\\s*(?:ID|NUMBER|#)?\\s*:\\s*([0-9A-Za-z-]+)/i);
    if (m && !keyValues['parcel id']) {
      addKV('parcel id', m[1]);
    }
    const f = txt.match(/FOLIO\\s*(?:ID|NUMBER|#)?\\s*:\\s*([0-9A-Za-z-]+)/i);
    if (f && !keyValues['folio']) {
      addKV('folio', f[1]);
    }
  });

  // Check URL query parameters for parcel ID
  const urlMatch = location.href.match(/[?&]parcel=([^&#]+)/i)
    || location.href.match(/[?&]folio=([^&#]+)/i)
    || location.href.match(/[?&]pin=([^&#]+)/i);
  if (urlMatch && !keyValues['parcel id']) {
    addKV('parcel id', decodeURIComponent(urlMatch[1]));
  }

  // 2. Structured Tables Categorization
  const salesHistory = [];
  const assessmentHistory = [];
  const buildings = [];
  const landLines = [];
  const extraFeatures = [];
  const otherTables = [];

  document.querySelectorAll('table').forEach(tbl => {
    const rows = Array.from(tbl.querySelectorAll('tr'));
    if (rows.length < 2) return;

    const headerRow = rows[0];
    const headerCells = Array.from(headerRow.querySelectorAll('th, td')).map(c => norm(c.innerText || c.textContent).toLowerCase());
    const headerStr = headerCells.join(' | ');

    const tableRows = [];
    rows.slice(1).forEach(tr => {
      const rowCells = Array.from(tr.querySelectorAll('td, th')).map(c => norm(c.innerText || c.textContent));
      if (rowCells.some(c => c.length > 0)) {
        const rowObj = {};
        headerCells.forEach((h, idx) => {
          if (rowCells[idx] !== undefined) rowObj[h || ('col_' + idx)] = rowCells[idx];
        });
        tableRows.push(rowObj);
      }
    });

    if (tableRows.length === 0) return;

    if (headerStr.includes('sale') || headerStr.includes('deed') || headerStr.includes('book') || headerStr.includes('grantor')) {
      salesHistory.push(...tableRows);
    } else if (headerStr.includes('year') && (headerStr.includes('value') || headerStr.includes('assessed') || headerStr.includes('just') || headerStr.includes('taxable') || headerStr.includes('mkt'))) {
      assessmentHistory.push(...tableRows);
    } else if (headerStr.includes('building') || headerStr.includes('bldg') || headerStr.includes('year blt') || headerStr.includes('living area') || headerStr.includes('heated') || headerStr.includes('actual') || headerStr.includes('base sf')) {
      buildings.push(...tableRows);
    } else if (headerStr.includes('land') || headerStr.includes('acre') || headerStr.includes('frontage') || headerStr.includes('depth') || headerStr.includes('zoning')) {
      landLines.push(...tableRows);
    } else if (headerStr.includes('feature') || headerStr.includes('yard') || headerStr.includes('outbuilding') || headerStr.includes('shed') || headerStr.includes('pool') || headerStr.includes('patio')) {
      extraFeatures.push(...tableRows);
    } else {
      otherTables.push({ headers: headerCells, rows: tableRows });
    }
  });

  return {
    key_values: keyValues,
    sales_history: salesHistory,
    assessment_history: assessmentHistory,
    buildings: buildings,
    land_lines: landLines,
    extra_features: extraFeatures,
    other_tables: otherTables,
    page_title: document.title,
    page_url: window.location.href,
  };
}
"""


def parse_dynamic_full_page_record(
    data: dict[str, Any],
    source_url: str = "",
    query_value: str = "",
) -> dict[str, Any]:
    """
    Parses a rich DOM extraction into a comprehensive, dynamic parcel details dictionary.
    Ensures that every section and table from the property card is preserved.
    """
    kv = data.get("key_values", {})
    bad_tokens = {"or", "and", "not", "null", "undefined", "none", "—", "-"}

    def _is_clean(val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip()
        if not s or s.lower() in bad_tokens:
            return False
        if "\u2195" in s or "↑" in s or "↓" in s:
            return False
        return True

    def _find_val(*keys: str) -> str | None:
        for k in keys:
            v = kv.get(k.lower())
            if _is_clean(v):
                return str(v).strip()
        for k in keys:
            k_lower = k.lower()
            for existing_k, existing_v in kv.items():
                if k_lower in existing_k and _is_clean(existing_v):
                    return str(existing_v).strip()
        return None

    def _parse_num(val_str: Any) -> float | None:
        if val_str is None:
            return None
        if isinstance(val_str, (int, float)):
            return float(val_str)
        m = re.search(r"[\d,]+(?:\.\d+)?", str(val_str).replace("$", ""))
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
        return None

    # Parcel ID
    parcel = (
        _find_val("parcel id", "folio", "pin", "parcel number", "parcel #", "parcel", "property id", "strap")
        or query_value
    )

    # Owner Name
    owner = (
        _find_val("owner name", "owner", "owner's name", "owner(s)", "primary owner", "owner 1", "property owner")
        or "OWNER OF RECORD"
    )

    # Mailing Address
    mailing = _find_val("mailing address", "mail address", "owner address", "mailing", "mailing1")
    csz = kv.get("city state zip") or kv.get("csz")
    if csz and mailing and csz not in mailing:
        mailing = f"{mailing} {csz}".strip()

    # Property / Site Address
    site_address = (
        _find_val("site address", "property address", "physical address", "situs address", "location")
        or ""
    )
    if not site_address and _is_clean(kv.get("address")):
        site_address = str(kv.get("address")).strip()

    # Property Use / Land Use
    prop_use = _find_val("property use", "use code", "use cd", "land use", "dor code", "property type")

    # Taxing District
    tax_dist = _find_val("taxing district", "tax dist", "district", "tax code", "millage code")

    # Land Size / Acres
    land_size = _find_val("land size(ac or lot)", "land size", "acres", "acreage", "lot size", "land area", "area")

    # Exemptions
    exemptions = _find_val("exemptions", "exemption", "homestead exemption", "exemption amount")

    # Map Number / POM
    map_no = _find_val("map number", "map no", "map #", "plat map", "pom")

    # Legal Description
    legal = _find_val("legal description", "legal", "short legal", "brief legal", "description", "1") or ""

    # Values
    assessed_val = _parse_num(_find_val("assessed value", "total assessed", "assessed", "tax val co", "total assessed value"))
    just_val = _parse_num(_find_val("just value", "market value", "just/market", "total just", "just", "mkt"))
    land_val = _parse_num(_find_val("land value", "land cm", "total land"))
    bldg_val = _parse_num(_find_val("building value", "bld", "improvement value", "total building"))
    taxable_val = _parse_num(_find_val("taxable value", "taxable", "county taxable", "school taxable"))

    ass_hist = data.get("assessment_history", [])
    if ass_hist:
        top_row = ass_hist[0]
        if assessed_val is None:
            for k, v in top_row.items():
                if "assessed" in k.lower():
                    assessed_val = _parse_num(v)
                    break
        if just_val is None:
            for k, v in top_row.items():
                if "just" in k.lower() or "market" in k.lower():
                    just_val = _parse_num(v)
                    break
        if land_val is None:
            for k, v in top_row.items():
                if "land" in k.lower():
                    land_val = _parse_num(v)
                    break
        if bldg_val is None:
            for k, v in top_row.items():
                if "bldg" in k.lower() or "building" in k.lower() or "improvement" in k.lower():
                    bldg_val = _parse_num(v)
                    break
        if taxable_val is None:
            for k, v in top_row.items():
                if "taxable" in k.lower():
                    taxable_val = _parse_num(v)
                    break

    year_built = _find_val("year built", "year blt", "actual year built", "eff year built")
    living_area = _find_val("living area", "heated area", "heated sq ft", "total sq ft", "actual area", "base sf")
    bedrooms = _find_val("bedrooms", "beds", "bed")
    bathrooms = _find_val("bathrooms", "baths", "bath")
    buildings_list = data.get("buildings", [])
    if buildings_list:
        b0 = buildings_list[0]
        if not year_built:
            for k, v in b0.items():
                if "year" in k.lower() or "blt" in k.lower():
                    year_built = str(v)
                    break
        if not living_area:
            for k, v in b0.items():
                if "living" in k.lower() or "heated" in k.lower() or "actual" in k.lower() or "sf" in k.lower():
                    living_area = str(v)
                    break
        if not bedrooms:
            for k, v in b0.items():
                if "bed" in k.lower():
                    bedrooms = str(v)
                    break
        if not bathrooms:
            for k, v in b0.items():
                if "bath" in k.lower():
                    bathrooms = str(v)
                    break

    sales_history = data.get("sales_history", [])
    land_lines = data.get("land_lines", [])
    extra_features = data.get("extra_features", [])

    return {
        "parcel_number": parcel,
        "owner_name": owner,
        "mailing_address": mailing,
        "property_address": site_address,
        "property_use": prop_use,
        "taxing_district": tax_dist,
        "land_size": land_size,
        "exemptions": exemptions,
        "map_number": map_no,
        "legal_description": legal,
        "assessed_value": assessed_val,
        "just_value": just_val or assessed_val,
        "market_value": just_val or assessed_val,
        "land_value": land_val,
        "improvement_value": bldg_val,
        "building_value": bldg_val,
        "taxable_value": taxable_val,
        "tax_year": _find_val("tax year", "year", "roll year") or "2024",
        "year_built": year_built,
        "living_area": living_area,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "values_history": ass_hist,
        "assessment_history": ass_hist,
        "sales_history": sales_history,
        "buildings": buildings_list,
        "land_lines": land_lines,
        "extra_features": extra_features,
        "sections": kv,
        "raw_data": {
            **kv,
            "sales_history": sales_history,
            "assessment_history": ass_hist,
            "buildings": buildings_list,
            "land_lines": land_lines,
            "extra_features": extra_features,
            "other_tables": data.get("other_tables", []),
            "source_url": source_url,
        },
    }
