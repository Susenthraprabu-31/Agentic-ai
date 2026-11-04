"""AI Page Analyzer & Recipe Generator.

Inspects county search portals, discovers tabs, form inputs, and buttons,
uses AI / semantic DOM heuristics to generate input-aware recipes,
and persists them to the PostgreSQL database.
"""

import asyncio
import re
import uuid
from typing import Any

from playwright.async_api import Page, async_playwright
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.browser.browser_manager import get_browser_context
from app.core.config import settings
from app.core.logging import get_logger
from app.models.county_source import CountySource, SourceConfiguration
from app.models.recipe import Recipe, RecipeLifecycleStatus, RecipeVersion

logger = get_logger(__name__)


class AIPageAnalyzerService:
    """Analyzes county search pages and generates multi-tab, input-aware search recipes."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def analyze_and_save_recipe(
        self,
        county_source_id: uuid.UUID,
        record_type: str = "assessor",
        portal_url: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        1. Loads the CountySource from the database.
        2. Opens the portal using Playwright and analyzes its DOM (tabs, inputs, buttons, frames).
        3. Generates multi-tab, input-conditional search steps.
        4. Saves the new Recipe and published RecipeVersion in the PostgreSQL database.
        5. Updates the active SourceConfiguration.
        """
        # 1. Fetch CountySource
        stmt = (
            select(CountySource)
            .where(CountySource.id == county_source_id)
            .options(selectinload(CountySource.configurations))
        )
        res = await self.db.execute(stmt)
        county = res.scalar_one_or_none()
        if not county:
            raise ValueError(f"CountySource {county_source_id} not found.")

        # Determine best search portal URL
        target_url = portal_url
        if not target_url and county.configurations:
            for cfg in county.configurations:
                if cfg.source_type == record_type:
                    target_url = cfg.search_page_url or cfg.website_url
                    break

        if not target_url:
            target_url = (county.assessor_url if record_type == "assessor" else county.tax_url) or ""

        # If URL is just root homepage, check if /Main_Search/search_rp.html is available
        if target_url and not any(p in target_url for p in ["search", "search_rp", "property", "find"]):
            candidate = target_url.rstrip("/") + "/Main_Search/search_rp.html"
            target_url = candidate

        if not target_url:
            raise ValueError(f"No portal URL found for CountySource {county.name} ({record_type}).")

        logger.info("Starting AI Page Analysis for %s County (%s) at %s", county.name, record_type, target_url)

        # 2. Inspect the live page with Playwright
        dom_analysis = await self._inspect_page_dom(target_url)

        # 3. Generate input-conditional recipe definition
        recipe_def = self._synthesize_recipe_definition(dom_analysis, target_url, county.name, record_type)

        # 4. Save into Database
        saved_info = await self._save_recipe_to_db(
            county_source_id=county_source_id,
            record_type=record_type,
            county_name=county.name,
            recipe_def=recipe_def,
            dom_analysis=dom_analysis,
            target_url=target_url,
            user_id=user_id,
        )

        return {
            "status": "success",
            "county_source_id": str(county_source_id),
            "county_name": county.name,
            "record_type": record_type,
            "target_url": target_url,
            "recipe_id": str(saved_info["recipe_id"]),
            "recipe_version_id": str(saved_info["recipe_version_id"]),
            "version_number": saved_info["version_number"],
            "discovered_tabs": dom_analysis.get("tabs", []),
            "discovered_inputs": dom_analysis.get("inputs", []),
            "discovered_buttons": dom_analysis.get("buttons", []),
            "steps": recipe_def["steps"],
            "field_mappings": recipe_def["field_mappings"],
        }

    async def _inspect_page_dom(self, url: str) -> dict[str, Any]:
        """Loads the portal, dismisses agreements, and extracts tabs, inputs, and search buttons across frames."""
        discovered: dict[str, Any] = {
            "has_disclaimer": False,
            "tabs": [],
            "inputs": [],
            "buttons": [],
            "results_selectors": [],
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            import re
            async def _fix_broken_map_scripts(route):
                try:
                    response = await route.fetch()
                    text = await response.text()
                    if "let Map" in text or "let Map;" in text or "let Map\n" in text or "let Map\r" in text:
                        fixed = re.sub(r'\blet\s+Map\b', 'let CollierMap', text)
                        fixed = re.sub(r'\bMap\b', 'CollierMap', fixed)
                        await route.fulfill(response=response, body=fixed)
                    else:
                        await route.fulfill(response=response, body=text)
                except Exception:
                    await route.continue_()

            try:
                await context.route("**/*Detail.js*", _fix_broken_map_scripts)
                await context.route("**/*detail.js*", _fix_broken_map_scripts)
            except Exception:
                pass

            page = await context.new_page()
            try:
                from app.browser.navigation import navigate_portal_step_by_step
                await navigate_portal_step_by_step(page=page, url=url, wait_for_mount=True)

                # Dynamically analyze and dismiss any disclaimer or modal using AI
                from app.browser.modal_detector import detect_and_dismiss_modal_ai
                modal_res = await detect_and_dismiss_modal_ai(page)
                if modal_res.get("has_disclaimer"):
                    discovered["has_disclaimer"] = True
                    discovered["disclaimer_action"] = modal_res
                    logger.info(
                        "AI Page Analyzer dismissed modal '%s' using selector: %s",
                        modal_res.get("dialog_title"),
                        modal_res.get("dismiss_selector")
                    )

                # Inspect all frames
                for f in page.frames:
                    # Discover tabs
                    tab_locators = f.locator("ul.ui-tabs-nav li a, div[role='tablist'] [role='tab'], .tab a, a[href^='#Tab']")
                    tab_count = await tab_locators.count()
                    for i in range(tab_count):
                        try:
                            t_elem = tab_locators.nth(i)
                            t_text = (await t_elem.inner_text()).strip()
                            t_href = await t_elem.get_attribute("href") or ""
                            t_id = await t_elem.get_attribute("id") or ""
                            if t_text:
                                discovered["tabs"].append({
                                    "text": t_text,
                                    "href": t_href,
                                    "id": t_id,
                                    "selector": f"a[href='{t_href}']" if t_href.startswith("#") else f"text='{t_text}'",
                                    "frame": f.name,
                                })
                        except Exception:
                            continue

                    # Discover input fields
                    input_locators = f.locator("input[type='text'], input:not([type]), select, input[placeholder], textarea")
                    inp_count = await input_locators.count()
                    for i in range(inp_count):
                        try:
                            inp = input_locators.nth(i)
                            inp_id = await inp.get_attribute("id") or ""
                            inp_name = await inp.get_attribute("name") or ""
                            inp_type = await inp.get_attribute("type") or "text"
                            inp_ph = await inp.get_attribute("placeholder") or ""
                            inp_bind = await inp.get_attribute("data-bind") or ""
                            inp_fc = await inp.get_attribute("formcontrolname") or ""
                            if inp_id or inp_name or inp_ph or inp_bind:
                                sel = f"#{inp_id}" if inp_id and not inp_id.isdigit() else (
                                    f"[name='{inp_name}']" if inp_name else (
                                        f"input[placeholder='{inp_ph}']" if inp_ph else f"input[formcontrolname='{inp_fc}']"
                                    )
                                )
                                discovered["inputs"].append({
                                    "id": inp_id,
                                    "name": inp_name,
                                    "placeholder": inp_ph,
                                    "dataBind": inp_bind,
                                    "formcontrolname": inp_fc,
                                    "type": inp_type,
                                    "selector": sel,
                                    "frame": f.name,
                                })
                        except Exception:
                            continue

                    # Discover radio buttons
                    radio_locators = f.locator("input[type='radio']")
                    radio_count = await radio_locators.count()
                    for i in range(radio_count):
                        try:
                            r = radio_locators.nth(i)
                            r_id = await r.get_attribute("id") or ""
                            r_name = await r.get_attribute("name") or ""
                            r_val = await r.get_attribute("value") or ""
                            r_lbl = ""
                            if r_id:
                                l_el = f.locator(f"label[for='{r_id}']").first
                                if await l_el.count() > 0:
                                    r_lbl = (await l_el.inner_text()).strip()
                            if not r_lbl:
                                try:
                                    r_lbl = (await r.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                                except Exception:
                                    pass
                            r_sel = f"#{r_id}" if r_id and not r_id.isdigit() else (
                                f"input[type='radio'][name='{r_name}'][value='{r_val}']" if r_name and r_val else (
                                    f"input[type='radio'][value='{r_val}']" if r_val else f"input[type='radio'][name='{r_name}']"
                                )
                            )
                            discovered.setdefault("radios", []).append({
                                "id": r_id,
                                "name": r_name,
                                "value": r_val,
                                "label": r_lbl,
                                "selector": r_sel,
                                "frame": f.name,
                            })
                        except Exception:
                            continue

                    # Discover buttons
                    btn_locators = f.locator("a.btnSearch, button[type='submit'], input[type='submit'], a[role='button'], button:has-text('Search'), button:has-text('SEARCH'), input[value*='Search' i]")
                    btn_count = await btn_locators.count()
                    for i in range(btn_count):
                        try:
                            b = btn_locators.nth(i)
                            b_id = await b.get_attribute("id") or ""
                            b_name = await b.get_attribute("name") or ""
                            b_text = (await b.inner_text()).strip()
                            if b_id or b_name or "search" in b_text.lower():
                                b_sel = f"#{b_id}" if b_id and not b_id.isdigit() else (
                                    f"[name='{b_name}']" if b_name else (
                                        f"button:has-text('{b_text}')" if b_text else "button[type='submit']"
                                    )
                                )
                                discovered["buttons"].append({
                                    "id": b_id,
                                    "name": b_name,
                                    "text": b_text,
                                    "selector": b_sel,
                                    "frame": f.name,
                                })
                        except Exception:
                            continue

            finally:
                await browser.close()

        logger.info(
            "Page analysis discovered %d tabs, %d inputs, %d buttons",
            len(discovered["tabs"]), len(discovered["inputs"]), len(discovered["buttons"])
        )
        return discovered

    def _synthesize_recipe_definition(
        self,
        dom: dict[str, Any],
        portal_url: str,
        county_name: str,
        record_type: str,
        executed_plan: dict[str, Any] | None = None,
        autocomplete_action: dict[str, Any] | None = None,
        result_click_action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synthesizes structured multi-tab, input-conditional recipe steps from DOM analysis and live execution plan."""
        steps: list[dict[str, Any]] = [
            {
                "action": "navigate",
                "url": portal_url,
                "description": f"Navigate to {county_name} County search portal",
                "timeout_ms": 25000,
            }
        ]

        disclaimer_act = dom.get("disclaimer_action")
        if disclaimer_act and disclaimer_act.get("has_disclaimer") and disclaimer_act.get("dismiss_selector"):
            saved_sel = disclaimer_act["dismiss_selector"]
            combined_sel = f"{saved_sel}, input[value*='agree' i], input[value*='continue' i], button:has-text('I Agree'), button:has-text('Agree'), button:has-text('Accept'), button:has-text('Continue'), button.btn-close, [aria-label*='close' i]"
            cleaned_parts = list(dict.fromkeys([p.strip() for p in combined_sel.split(",") if p.strip()]))
            steps.append({
                "action": "click_if_visible",
                "selector": ", ".join(cleaned_parts),
                "description": disclaimer_act.get("description") or f"Dismiss '{disclaimer_act.get('dialog_title', 'terms')}' modal",
                "optional": True,
            })
        elif dom.get("has_disclaimer"):
            # Fallback if detected but without custom action
            steps.append({
                "action": "click_if_visible",
                "selector": "input[value*='agree' i], input[value*='continue' i], button:has-text('OK'), button:has-text('I Agree'), button:has-text('Agree'), button:has-text('Accept'), button:has-text('Continue'), a:has-text('I Agree'), button.btn-close, [aria-label*='close' i]",
                "description": "Dismiss terms of use / disclaimer modal if presented",
                "optional": True,
            })

        # Collect all inputs, tabs, radios, and buttons across DOM
        all_elements = dom.get("elements", [])
        all_inputs = [*dom.get("inputs", []), *[e for e in all_elements if e.get("kind") == "input"]]
        all_tabs = [*dom.get("tabs", []), *[e for e in all_elements if e.get("kind") == "tab"]]
        all_radios = [*dom.get("radios", []), *[e for e in all_elements if e.get("kind") == "radio"]]
        all_buttons = [*dom.get("buttons", []), *[e for e in all_elements if e.get("kind") in ("search_button", "button")]]

        # Match tabs
        parcel_tab = None
        address_tab = None
        owner_tab = None

        for t in all_tabs:
            text_lower = (t.get("text") or "").lower()
            href_lower = (t.get("href") or "").lower()
            if not parcel_tab and any(k in text_lower or k in href_lower for k in ["parcel", "pin", "folio", "apn"]):
                parcel_tab = t
            elif not address_tab and any(k in text_lower or k in href_lower for k in ["address", "site", "location", "street"]):
                address_tab = t
            elif not owner_tab and any(k in text_lower or k in href_lower for k in ["owner", "name"]):
                owner_tab = t

        # Match radios
        parcel_radio = None
        for r in all_radios:
            lbl = (r.get("label") or "").lower()
            val = (r.get("value") or "").lower()
            if any(k in lbl or k in val for k in ["folio", "parcel", "pin"]):
                parcel_radio = r
                break

        # Match inputs
        parcel_input = None
        address_input = None
        owner_input = None

        for inp in all_inputs:
            ident = f"{inp.get('id', '')} {inp.get('name', '')} {inp.get('placeholder', '')} {inp.get('formcontrolname', '')} {inp.get('dataBind', '')}".lower()
            ph = inp.get("placeholder", "")
            if not parcel_input:
                if any(k in ident for k in ["parcel", "folio", "pin", "strap", "apn"]):
                    parcel_input = inp
                elif re.search(r"ex:\s*[\d\-]+", ph, re.I):
                    parcel_input = inp
            if not address_input:
                if any(k in ident for k in ["address", "street", "site", "location"]):
                    address_input = inp
                elif re.search(r"ex:\s*.*(?:street|ave|blvd|rd|dr|armenia|main)", ph, re.I):
                    address_input = inp
            if not owner_input:
                if any(k in ident for k in ["owner", "name"]):
                    owner_input = inp
                elif re.search(r"ex:\s*.*(?:smith|doe|john)", ph, re.I):
                    owner_input = inp

        # Priority: If executed_plan provided exact verified selectors from live execution, use them!
        executed_qt = (executed_plan.get("query_type") or "parcel").lower() if executed_plan else None

        parcel_tab_sel = parcel_tab["selector"] if parcel_tab else None
        parcel_radio_sel = (executed_plan.get("radio_selector") if executed_qt == "parcel" else None) or (parcel_radio["selector"] if parcel_radio else None)
        parcel_inp_sel = (executed_plan.get("input_selector") if executed_qt == "parcel" else None) or (parcel_input["selector"] if parcel_input else 'input[placeholder*="parcel" i], input[placeholder*="folio" i], input[placeholder*="pin" i], input[name*="parcel" i], input[id*="parcel" i]')
        parcel_btn_sel = (executed_plan.get("submit_selector") if executed_qt == "parcel" else None) or 'button:has-text("Search"), button[type="submit"], input[type="submit"], [aria-label*="search" i]'

        address_tab_sel = address_tab["selector"] if address_tab else None
        address_inp_sel = (executed_plan.get("input_selector") if executed_qt == "address" else None) or (address_input["selector"] if address_input else 'input[placeholder*="address" i], input[placeholder*="street" i], input[name*="address" i], input[id*="address" i]')
        address_btn_sel = (executed_plan.get("submit_selector") if executed_qt == "address" else None) or 'button:has-text("Search"), button[type="submit"], input[type="submit"], [aria-label*="search" i]'

        owner_tab_sel = owner_tab["selector"] if owner_tab else None
        owner_inp_sel = (executed_plan.get("input_selector") if executed_qt == "owner" else None) or (owner_input["selector"] if owner_input else 'input[placeholder*="owner" i], input[placeholder*="name" i], input[name*="owner" i], input[id*="owner" i]')
        owner_btn_sel = (executed_plan.get("submit_selector") if executed_qt == "owner" else None) or 'button:has-text("Search"), button[type="submit"], input[type="submit"], [aria-label*="search" i]'

        # Match specific buttons by ID if present and not overridden by plan
        if not (executed_plan and executed_qt == "parcel" and executed_plan.get("submit_selector")):
            for b in all_buttons:
                b_id = b.get("id", "").lower()
                b_sel = b.get("selector", "")
                if "parcel" in b_id or "searchparcel" in b_id:
                    parcel_btn_sel = b_sel or f"#{b['id']}"
                    break
        if not (executed_plan and executed_qt == "address" and executed_plan.get("submit_selector")):
            for b in all_buttons:
                b_id = b.get("id", "").lower()
                b_sel = b.get("selector", "")
                if "address" in b_id or "street" in b_id or "searchaddress" in b_id:
                    address_btn_sel = b_sel or f"#{b['id']}"
                    break
        if not (executed_plan and executed_qt == "owner" and executed_plan.get("submit_selector")):
            for b in all_buttons:
                b_id = b.get("id", "").lower()
                b_sel = b.get("selector", "")
                if ("owner" in b_id or "searchname" in b_id) and "sub" not in b_id:
                    owner_btn_sel = b_sel or f"#{b['id']}"
                    break

        # 1. Parcel ID Conditional Branch
        if parcel_tab_sel:
            steps.append({
                "action": "click",
                "selector": parcel_tab_sel,
                "condition": "input.parcel_id",
                "description": "Click Parcel ID search tab",
            })
        if parcel_radio_sel:
            steps.append({
                "action": "click_if_visible",
                "selector": parcel_radio_sel,
                "condition": "input.parcel_id",
                "description": "Select Folio / Parcel Number radio option",
                "optional": True,
            })
        steps.extend([
            {
                "action": "wait_for_selector",
                "selector": parcel_inp_sel,
                "condition": "input.parcel_id",
                "description": "Wait for Parcel ID input field",
            },
            {
                "action": "fill",
                "selector": parcel_inp_sel,
                "value": "{{input.parcel_id}}",
                "condition": "input.parcel_id",
                "description": "Enter Parcel ID into search field",
            },
        ])
        if autocomplete_action and autocomplete_action.get("detected"):
            steps.append({
                "action": "click_if_visible",
                "selector": autocomplete_action.get("selector") or ".ui-autocomplete li:visible, .ui-menu-item:visible, [role='option']:visible",
                "condition": "input.parcel_id",
                "description": "Select autocomplete suggestion if presented",
                "optional": True,
                "timeout_ms": 3000,
            })
        steps.append({
            "action": "click",
            "selector": parcel_btn_sel,
            "condition": "input.parcel_id",
            "description": "Click Search for Parcel ID",
        })

        # 2. Site Address Conditional Branch
        if address_tab_sel:
            steps.append({
                "action": "click",
                "selector": address_tab_sel,
                "condition": "input.address",
                "description": "Click Site Address search tab",
            })
        steps.extend([
            {
                "action": "wait_for_selector",
                "selector": address_inp_sel,
                "condition": "input.address",
                "description": "Wait for Site Address input field",
            },
            {
                "action": "fill",
                "selector": address_inp_sel,
                "value": "{{input.address}}",
                "condition": "input.address",
                "description": "Enter property address into search field",
            },
            {
                "action": "click",
                "selector": address_btn_sel,
                "condition": "input.address",
                "description": "Click Search for Site Address",
            },
        ])

        # 3. Owner Name Conditional Branch
        if owner_tab_sel:
            steps.append({
                "action": "click",
                "selector": owner_tab_sel,
                "condition": "input.owner_name",
                "description": "Click Owner Name search tab",
            })
        steps.extend([
            {
                "action": "wait_for_selector",
                "selector": owner_inp_sel,
                "condition": "input.owner_name",
                "description": "Wait for Owner Name input field",
            },
            {
                "action": "fill",
                "selector": owner_inp_sel,
                "value": "{{input.owner_name}}",
                "condition": "input.owner_name",
                "description": "Enter owner name into search field",
            },
            {
                "action": "click",
                "selector": owner_btn_sel,
                "condition": "input.owner_name",
                "description": "Click Search for Owner Name",
            },
        ])

        # 4. Wait for Results (Both Grid Table and Direct Detail Page)
        steps.append({
            "action": "wait_for_selector",
            "selector": "table tbody tr, [role='row'], td:has-text('Site Location'), td:has-text('Name / Address'), table tr td a",
            "description": "Wait for search results table or property detail record to load",
            "timeout_ms": 25000,
            "optional": True,
        })

        # 5. Dynamic Detail Navigation (Click parcel number or matching result record if a results table appears)
        result_click_info = result_click_action or dom.get("result_click_action")
        click_target = None
        if result_click_info:
            click_target = result_click_info.get("selector") or result_click_info.get("click_selector")

        generic_result_sel = "table tbody tr td.link, td[data-bind*='goTo'], td[data-bind*='goToPin'], table tr:not(:first-child) td a, a[href*='PropertySearch'], a[href*='propertydetails' i], a[href*='detail' i], a[href*='parcel' i], a[href*='RecordDetail' i], a[href*='FolioID' i], table tbody tr td.pointer"
        final_result_sel = f"{click_target}, {generic_result_sel}" if click_target else generic_result_sel

        steps.append({
            "action": "click_if_visible",
            "selector": final_result_sel,
            "description": (result_click_info.get("description") if result_click_info else None) or "Click matching parcel number or result record in search results table to view property details",
            "optional": True,
            "timeout_ms": 10000,
        })

        field_mappings = {
            "parcel_number": {
                "selector": "[data-bind*='displayFolio'], [data-bind*='pin'], [data-bind*='folio'], [data-bind*='parcel'], td:has-text('Parcel') + td, td:has-text('Folio') + td, td.parcel",
                "source_column": "Parcel ID",
            },
            "owner_name": {
                "selector": "[data-bind*='publicOwner'], [data-bind*='owner'], td:has-text('Owner') + td, td:has-text('Name') + td, td.owner",
                "source_column": "Owner Name",
            },
            "property_address": {
                "selector": "[data-bind*='siteAddress'], [data-bind*='address'], td:has-text('Address') + td, td:has-text('Location') + td, td.address",
                "source_column": "Property Address",
            },
            "assessed_value": {
                "selector": "[data-bind*='valueSummary'], [data-bind*='assessed'], td:has-text('Assessed') + td, td:has-text('Just') + td, td.assessed",
                "source_column": "Assessed Value",
            },
            "legal_description": {
                "selector": "[data-bind*='legalDescription'], [data-bind*='legal'], td:has-text('Legal') + td",
                "source_column": "Legal Description",
            },
        }

        table_settings = {
            "row_selector": "table tbody tr, [role='row']",
            "columns": {
                "parcel_number": "td.link, td[data-bind*='goTo'], td:first-child",
                "owner_name": "td:nth-child(2)",
                "property_address": "td:nth-child(3)",
                "assessed_value": "td:nth-child(4)",
            },
        }

        return {
            "steps": steps,
            "field_mappings": field_mappings,
            "table_settings": table_settings,
        }

    async def _save_recipe_to_db(
        self,
        county_source_id: uuid.UUID,
        record_type: str,
        county_name: str,
        recipe_def: dict[str, Any],
        dom_analysis: dict[str, Any],
        target_url: str,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Persists the generated Recipe, RecipeVersion, and updates SourceConfiguration in PostgreSQL."""
        # 1. Find or create Recipe
        rec_stmt = (
            select(Recipe)
            .where(
                Recipe.county_source_id == county_source_id,
                Recipe.record_type == record_type,
            )
            .options(selectinload(Recipe.versions))
        )
        rec_res = await self.db.execute(rec_stmt)
        recipe = rec_res.scalars().first()

        recipe_name = f"{county_name} County {record_type.capitalize()} Property Search"
        if not recipe:
            recipe = Recipe(
                name=recipe_name,
                county_source_id=county_source_id,
                record_type=record_type,
                status=RecipeLifecycleStatus.published.value,
                description=f"AI-generated multi-tab search recipe for {county_name} County.",
                created_by=user_id,
            )
            self.db.add(recipe)
            await self.db.flush()
        else:
            recipe.status = RecipeLifecycleStatus.published.value

        # Calculate next version number
        v_stmt = select(RecipeVersion).where(RecipeVersion.recipe_id == recipe.id)
        v_res = await self.db.execute(v_stmt)
        existing_versions = v_res.scalars().all()
        max_v = max([v.version_number for v in existing_versions], default=0)
        next_v = max_v + 1

        # Unpublish old versions
        for v in existing_versions:
            v.is_published = False

        # Create new published RecipeVersion
        new_version = RecipeVersion(
            recipe_id=recipe.id,
            version_number=next_v,
            definition=recipe_def,
            notes="Generated by AI Page Analyzer with input-based multi-tab switching.",
            is_published=True,
            is_current=True,
            lifecycle_status=RecipeLifecycleStatus.published.value,
            created_by=user_id,
        )
        self.db.add(new_version)
        await self.db.flush()

        # Update SourceConfiguration
        sc_stmt = select(SourceConfiguration).where(
            SourceConfiguration.county_source_id == county_source_id,
            SourceConfiguration.source_type == record_type,
        )
        sc_res = await self.db.execute(sc_stmt)
        source_config = sc_res.scalar_one_or_none()

        # Dynamically derive search_fields and search_button from synthesized recipe steps
        discovered_parcel_sel = 'input[placeholder*="parcel" i], input[placeholder*="folio" i], input[type="text"]'
        discovered_address_sel = 'input[placeholder*="address" i], input[placeholder*="street" i], input[type="text"]'
        discovered_owner_sel = 'input[placeholder*="owner" i], input[placeholder*="name" i], input[type="text"]'
        discovered_btn_sel = 'button:has-text("Search"), button[type="submit"], input[type="submit"]'

        for s in recipe_def.get("steps", []):
            action = s.get("action")
            cond = str(s.get("condition", ""))
            sel = s.get("selector")
            if not sel:
                continue
            if action == "fill":
                if "parcel" in cond:
                    discovered_parcel_sel = sel
                elif "address" in cond:
                    discovered_address_sel = sel
                elif "owner" in cond:
                    discovered_owner_sel = sel
            elif action == "click" and any(k in cond for k in ["parcel", "search"]):
                discovered_btn_sel = sel

        search_fields = {
            "parcel_id": {"selector": discovered_parcel_sel, "name": "Parcel ID"},
            "address": {"selector": discovered_address_sel, "name": "Site Address"},
            "owner_name": {"selector": discovered_owner_sel, "name": "Owner Name"},
        }
        search_button = {"selector": discovered_btn_sel}
        navigation_config = {"tabs": dom_analysis.get("tabs", [])}

        if source_config:
            source_config.website_url = target_url
            source_config.search_page_url = target_url
            source_config.search_fields = search_fields
            source_config.search_button = search_button
            source_config.navigation_config = navigation_config
            source_config.is_active = True
            source_config.status = "configured"
        else:
            source_config = SourceConfiguration(
                county_source_id=county_source_id,
                source_type=record_type,
                website_url=target_url,
                search_page_url=target_url,
                search_fields=search_fields,
                search_button=search_button,
                navigation_config=navigation_config,
                is_active=True,
                status="configured",
                created_by=user_id,
            )
            self.db.add(source_config)
            await self.db.flush()

        recipe.source_configuration_id = source_config.id
        await self.db.commit()

        logger.info(
            "Successfully saved AI Recipe %s (v%d, id: %s) in PostgreSQL",
            recipe.name, next_v, new_version.id
        )

        return {
            "recipe_id": recipe.id,
            "recipe_version_id": new_version.id,
            "version_number": next_v,
        }
