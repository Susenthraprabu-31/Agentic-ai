from app.browser.miami_dade_tax_auth import (
    is_miami_dade_tax_portal,
    playwright_cookies_from_auth,
)


def test_is_miami_dade_tax_portal_true():
    assert is_miami_dade_tax_portal("https://county-taxes.net/fl-miamidade/property-tax/")


def test_is_miami_dade_tax_portal_false_assessor():
    assert not is_miami_dade_tax_portal("https://apps.miamidade.gov/PropertySearch/#/")


def test_is_miami_dade_tax_portal_false_other_county():
    assert not is_miami_dade_tax_portal("https://county-taxes.net/fl-broward/property-tax/")


def test_playwright_cookies_strip_partition_key():
    raw = [
        {
            "name": "sid",
            "value": "abc",
            "domain": "county-taxes.net",
            "path": "/",
            "expires": -1,
            "partitionKey": {"topLevelSite": "https://county-taxes.net"},
            "sameSite": "Lax",
        }
    ]
    out = playwright_cookies_from_auth(raw)
    assert len(out) == 1
    assert out[0]["name"] == "sid"
    assert "partitionKey" not in out[0]
    assert "expires" not in out[0]
    assert out[0]["sameSite"] == "Lax"