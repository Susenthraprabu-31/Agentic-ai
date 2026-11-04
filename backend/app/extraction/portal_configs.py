"""Florida county property portal URLs and parcel/address normalization."""
from __future__ import annotations

import re
from typing import Optional

COLLIER_SEARCH_URL = "https://www.collierappraiser.com/Main_Search/search_rp.html"
COLLIER_HOME_URL = "https://www.collierappraiser.com/"
MIAMI_DADE_SEARCH_URL = "https://apps.miamidade.gov/PropertySearch/#/"
ORANGE_SEARCH_URL = "https://ocpaweb.ocpafl.org/parcelsearch"
BREVARD_SEARCH_URL = "https://www.bcpao.us/propertysearch/#/nav/search"


def normalize_florida_parcel(parcel: str, county: Optional[str] = None) -> str:
    """Normalize parcel/folio/PIN for Florida county searches."""
    cleaned = parcel.strip()
    if not cleaned:
        return cleaned

    digits = re.sub(r"\D", "", cleaned)

    if county == "collier":
        return digits or cleaned

    if county == "brevard" or re.search(r"[A-Za-z*]", cleaned):
        return re.sub(r"\s+", "", cleaned).upper()

    if county == "orange" or (county is None and len(digits) in (10, 12, 15, 16)):
        if len(digits) >= 10:
            d = digits[:16].zfill(16) if len(digits) < 16 else digits[:16]
            return f"{d[0:2]}-{d[2:4]}-{d[4:6]}-{d[6:10]}-{d[10:12]}-{d[12:16]}"
        return cleaned

    if county == "miami-dade" or (county is None and len(digits) == 13):
        if len(digits) == 13:
            return f"{digits[0:2]}-{digits[2:6]}-{digits[6:9]}-{digits[9:13]}"
        return digits or cleaned

    if county in ("broward", "hillsborough"):
        return digits or cleaned

    if len(digits) in (11, 14):
        return normalize_florida_pa_parcel(cleaned)

    if len(digits) >= 6:
        return digits

    return cleaned


def normalize_florida_pa_parcel(parcel: str) -> str:
    """Format parcel for floridapa.com / custom PA GIS."""
    stripped = parcel.strip()
    if re.match(r"^\d{2}-\d{2}-\d{2}-\d{5}-\d{3}$", stripped):
        return stripped
    if re.match(r"^\d{5}-\d-\d{5}$", stripped):
        return stripped
    digits = re.sub(r"\D", "", parcel)
    if len(digits) == 11:
        d = digits.zfill(11)
        return f"{d[0:5]}-{d[5]}-{d[6:11]}"
    if len(digits) >= 14:
        d = digits[:14]
        return f"{d[0:2]}-{d[2:4]}-{d[4:6]}-{d[6:11]}-{d[11:14]}"
    return stripped


def format_florida_pa_address_for_search(address: str) -> str:
    """Normalize address for floridapa StreetName (*HouseNumber StreetName in one field)."""
    cleaned = re.sub(r"\s+", " ", address.strip())
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    return cleaned


def format_miami_dade_address_for_search(address: str) -> str:
    """Normalize address for Miami-Dade PA search (street before first comma)."""
    return format_florida_pa_address_for_search(address)


def is_miami_dade_assessor(url: str) -> bool:
    lower = url.lower()
    return ("miamidadepa.gov" in lower or "miamidade.gov" in lower) and "propertysearch" in lower


def is_collier_assessor(url: str) -> bool:
    return "collierappraiser.com" in url.lower()


def is_orange_county_assessor(url: str) -> bool:
    return "ocpafl.org" in url.lower()


def is_brevard_assessor(url: str) -> bool:
    return "bcpao.us" in url.lower()


def is_broward_assessor(url: str) -> bool:
    return "bcpa.net" in url.lower()


def is_hillsborough_assessor(url: str) -> bool:
    return "hcpafl.org" in url.lower()


def is_florida_schneider_portal(url: str) -> bool:
    lower = url.lower()
    return ("schneidercorp.com" in lower or "qpublic.net" in lower) and any(
        fl_tok in lower for fl_tok in ("/fl/", "florida", "qpublic.schneidercorp.com")
    )


def resolve_florida_assessor_url(assessor_url: str) -> str:
    """Normalize Florida assessor URL to its primary search endpoint."""
    if not assessor_url or not assessor_url.strip():
        return ""
    clean = assessor_url.strip()
    if is_collier_assessor(clean) and "search_rp" not in clean:
        return COLLIER_SEARCH_URL
    if is_miami_dade_assessor(clean) and "#/" not in clean:
        return MIAMI_DADE_SEARCH_URL
    return clean

