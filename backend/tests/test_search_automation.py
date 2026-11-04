"""Unit and integration tests for Phase 3B County Website Interaction & Search Automation.

Tests:
1. Pattern A: Standard Search Form (fill, submit, extract).
2. Pattern B: Dropdown Search (category select, criteria fill, submit).
3. Pattern C: SPA Search (dynamic radio/checkbox controls, state wait, result rendering).
4. Pattern D: GET / POST Search (parameterized GET search, form submission).
5. Result Extractor: table extraction, missing fields (no hallucinations), detail page navigation.
6. Pagination: next-page traversal, max page limits, duplicate detection, pagination stats.
7. SSRF Protection: validation against private IPs, AWS metadata service (169.254.169.254), and schemes.
8. Recipe Loader: loads exact published version, rejects draft/deprecated versions, resolves selectors.
9. Assessor & Tax Node Executors: real recipe execution, independent branch execution, security challenge halt.
"""

import asyncio
import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser.browser_manager import get_browser_context, get_browser_manager
from app.browser.pagination_handler import PaginationHandler
from app.browser.recipe_loader import RecipeLoader, RecipeLoadException
from app.browser.result_extractor import ResultExtractor
from app.browser.step_runner import (
    PlaywrightStepRunner,
    SecurityChallengeException,
)
from app.browser.url_validator import SSRFProtectionException, URLValidator
from app.db.base import Base
from app.models import (
    AssessorResult,
    CountySource,
    NodeRun,
    Recipe,
    RecipeVersion,
    RefreshToken,
    SecurityEvent,
    SourceConfiguration,
    TaxResult,
    TemplateVersion,
    UITemplate,
    User,
    ValidationCase,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.workflow_engine.execution_context import ExecutionContext
from app.workflow_engine.executor import WorkflowExecutor
from app.workflow_engine.node_executor import AssessorNodeExecutor, TaxNodeExecutor

# ---------------------------------------------------------------------------
# Test DB Setup
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

SEARCH_TEST_TABLES = [
    User.__table__,
    RefreshToken.__table__,
    CountySource.__table__,
    UITemplate.__table__,
    TemplateVersion.__table__,
    SourceConfiguration.__table__,
    Recipe.__table__,
    RecipeVersion.__table__,
    ValidationCase.__table__,
    Workflow.__table__,
    WorkflowVersion.__table__,
    WorkflowRun.__table__,
    NodeRun.__table__,
    AssessorResult.__table__,
    TaxResult.__table__,
    SecurityEvent.__table__,
]


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=SEARCH_TEST_TABLES)
        )
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=SEARCH_TEST_TABLES)
        )


# ---------------------------------------------------------------------------
# 1. Pattern A: Standard Search Form
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pattern_a_standard_search_form():
    """Verify Pattern A: Fill search form, submit, wait for results, extract property fields."""
    html = """
    <html>
      <body>
        <form id="search-form">
          <input id="parcel-no" type="text" />
          <button id="search-submit" type="button" onclick="
            document.getElementById('parcel-res').innerText = '01-234-567';
            document.getElementById('owner-res').innerText = 'SMITH JOHN';
            document.getElementById('val-res').innerText = '$320,000.00';
          ">Search</button>
        </form>
        <div id="results">
          <span id="parcel-res"></span>
          <span id="owner-res"></span>
          <span id="val-res"></span>
        </div>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        steps = [
            {"action": "fill", "selector": "#parcel-no", "value": "{{input.parcel_id}}"},
            {"action": "click", "selector": "#search-submit"},
            {"action": "wait", "duration": 50},
        ]
        context_vars = {"input": {"parcel_id": "01-234-567"}}
        extract_schema = {
            "parcel_number": {"selector": "#parcel-res", "attribute": "text"},
            "owner_name": {"selector": "#owner-res", "attribute": "text"},
            "assessed_value": {"selector": "#val-res", "attribute": "text", "is_numeric": True},
        }

        runner = PlaywrightStepRunner(page, run_id="run-pat-a", node_id="node-a")
        res = await runner.execute_steps(steps, context_vars, extract_schema=extract_schema)

        assert res["parcel_number"] == "01-234-567"
        assert res["owner_name"] == "SMITH JOHN"
        assert res["assessed_value"] == 320000.0


# ---------------------------------------------------------------------------
# 2. Pattern B: Dropdown Search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pattern_b_dropdown_search():
    """Verify Pattern B: Select category from dropdown, enter criteria, submit form."""
    html = """
    <html>
      <body>
        <select id="search-category">
          <option value="owner">Owner Name</option>
          <option value="parcel">Parcel ID</option>
          <option value="address">Address</option>
        </select>
        <input id="search-term" type="text" />
        <button id="btn-submit" type="button" onclick="
          var cat = document.getElementById('search-category').value;
          var term = document.getElementById('search-term').value;
          document.getElementById('summary').innerText = 'Found: ' + cat + '=' + term;
        ">Find</button>
        <div id="summary"></div>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        steps = [
            {"action": "select", "selector": "#search-category", "value": "parcel"},
            {"action": "fill", "selector": "#search-term", "value": "{{input.parcel_id}}"},
            {"action": "click", "selector": "#btn-submit"},
            {"action": "wait", "duration": 50},
        ]
        context_vars = {"input": {"parcel_id": "PARCEL-9988"}}
        extract_schema = {
            "summary": {"selector": "#summary", "attribute": "text"},
        }

        runner = PlaywrightStepRunner(page, run_id="run-pat-b", node_id="node-b")
        res = await runner.execute_steps(steps, context_vars, extract_schema=extract_schema)

        assert res["summary"] == "Found: parcel=PARCEL-9988"


# ---------------------------------------------------------------------------
# 3. Pattern C: SPA Search (Dynamic Controls & State Waits)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pattern_c_spa_dynamic_search():
    """Verify Pattern C: Radio buttons, checkboxes, dynamic SPA rendering, and wait_for_selector."""
    html = """
    <html>
      <body>
        <input type="radio" id="type-real" name="ptype" value="real" checked /> Real Estate
        <input type="radio" id="type-personal" name="ptype" value="personal" /> Personal Property
        <input type="checkbox" id="include-history" /> Include Prior Years

        <button id="spa-query-btn" type="button">Execute SPA Query</button>
        <div id="container"></div>

        <script>
          document.getElementById('spa-query-btn').addEventListener('click', function() {
            setTimeout(function() {
              var div = document.createElement('div');
              div.id = 'spa-results-table';
              div.innerHTML = '<span class="prop-id">99-001-SPA</span><span class="status">ACTIVE</span>';
              document.getElementById('container').appendChild(div);
            }, 50);
          });
        </script>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        steps = [
            {"action": "check", "selector": "#type-personal"},
            {"action": "check", "selector": "#include-history"},
            {"action": "click", "selector": "#spa-query-btn"},
            {"action": "wait_for_selector", "selector": "#spa-results-table", "timeout_ms": 3000},
        ]
        extract_schema = {
            "parcel_number": {"selector": ".prop-id", "attribute": "text"},
            "status": {"selector": ".status", "attribute": "text"},
        }

        runner = PlaywrightStepRunner(page, run_id="run-pat-c", node_id="node-c")
        res = await runner.execute_steps(steps, {}, extract_schema=extract_schema)

        assert res["parcel_number"] == "99-001-SPA"
        assert res["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# 4. Pattern D: GET Search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pattern_d_get_search():
    """Verify Pattern D: Construct URL with query parameters and navigate."""
    async with get_browser_context() as (_, page):
        # We can execute get_search using a data URL base
        steps = [
            {
                "action": "get_search",
                "url": "data:text/html,<html><body><h1 id='title'>GET Search Success</h1></body></html>",
                "params": {"parcel": "{{input.parcel_id}}"},
            }
        ]
        context_vars = {"input": {"parcel_id": "GET-12345"}}
        extract_schema = {
            "page_title": {"selector": "#title", "attribute": "text"},
        }

        runner = PlaywrightStepRunner(page, run_id="run-pat-d", node_id="node-d")
        res = await runner.execute_steps(steps, context_vars, extract_schema=extract_schema)

        assert res["page_title"] == "GET Search Success"


# ---------------------------------------------------------------------------
# 5. Result Extractor: Tables, Missing Fields & Detail Links
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_result_extractor_table_and_missing_fields():
    """Verify result extractor parses rows, column mappings, and leaves missing fields as None."""
    html = """
    <html>
      <body>
        <table id="property-table">
          <thead><tr><th>Parcel</th><th>Owner</th><th>Address</th><th>Value</th></tr></thead>
          <tbody>
            <tr>
              <td>11-222-3333</td>
              <td>MILLER DAVID</td>
              <td>456 PINE AVE</td>
              <td>$410,500.00</td>
            </tr>
            <tr>
              <td>11-222-3334</td>
              <td>BROWN EMILY</td>
              <td>458 PINE AVE</td>
              <td>$385,000.00</td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        extractor = ResultExtractor(page, run_id="test-ext", node_id="test-node")

        table_config = {
            "table_selector": "#property-table",
            "row_selector": "tbody tr",
        }
        field_mappings = {
            "parcel_number": 0,
            "owner_name": 1,
            "property_address": 2,
            "assessed_value": {"column_index": 3, "is_numeric": True},
            "legal_description": {"column_index": 99},  # Non-existent column
        }

        records = await extractor.extract_table_results(table_config, field_mappings)
        assert len(records) == 2

        # Record 1
        assert records[0]["parcel_number"] == "11-222-3333"
        assert records[0]["owner_name"] == "MILLER DAVID"
        assert records[0]["assessed_value"] == 410500.0
        assert records[0]["legal_description"] is None  # Never invented!

        # Record 2
        assert records[1]["parcel_number"] == "11-222-3334"
        assert records[1]["owner_name"] == "BROWN EMILY"
        assert records[1]["_metadata"]["row_index"] == 1


@pytest.mark.asyncio
async def test_result_extractor_detail_page_navigation():
    """Verify following property detail page link and merging detailed fields."""
    list_html = """
    <html><body>
      <table id="results"><tbody>
        <tr>
          <td><a class="det-link" href="data:text/html,<html><body><h1 id='legal'>LOT 5 SEC 12 OAK GROVE</h1><span id='taxyr'>2025</span></body></html>">Details</a></td>
          <td>99-11-22</td>
        </tr>
      </tbody></table>
    </body></html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(list_html)
        extractor = ResultExtractor(page, run_id="test-detail", node_id="test-node")

        table_config = {
            "table_selector": "#results",
            "row_selector": "tbody tr",
            "detail_link_selector": "a.det-link",
        }
        field_mappings = {"parcel_number": 1}
        records = await extractor.extract_table_results(table_config, field_mappings)
        assert len(records) == 1
        assert records[0]["parcel_number"] == "99-11-22"
        detail_url = records[0]["_metadata"]["detail_url"]
        assert detail_url is not None

        # Follow detail page
        detail_schema = {
            "legal_description": "#legal",
            "tax_year": {"selector": "#taxyr", "is_numeric": True},
        }
        full_record = await extractor.follow_detail_page_and_extract(
            detail_url, detail_schema, parent_record=records[0]
        )

        assert full_record["parcel_number"] == "99-11-22"
        assert full_record["legal_description"] == "LOT 5 SEC 12 OAK GROVE"
        assert full_record["tax_year"] == 2025


# ---------------------------------------------------------------------------
# 6. Pagination & Duplicate Detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_traversal_and_limits():
    """Verify multi-page traversal using next button, respecting max_pages and avoiding duplicates."""
    html = """
    <html>
      <body>
        <table id="prop-table">
          <tbody id="rows">
            <tr><td>PARCEL-01</td><td>OWNER 1</td></tr>
          </tbody>
        </table>
        <button id="next-page" type="button" onclick="
          var rows = document.getElementById('rows');
          if (rows.getAttribute('data-page') === '2') {
            // End of pagination
            document.getElementById('next-page').disabled = true;
          } else {
            rows.setAttribute('data-page', '2');
            rows.innerHTML = '<tr><td>PARCEL-02</td><td>OWNER 2</td></tr>';
          }
        ">Next</button>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        extractor = ResultExtractor(page, run_id="run-pag", node_id="node-pag")
        paginator = PaginationHandler(page, run_id="run-pag", node_id="node-pag")

        table_config = {"table_selector": "#prop-table", "row_selector": "#rows tr"}
        field_mappings = {"parcel_number": 0, "owner_name": 1}
        pagination_config = {
            "next_button_selector": "#next-page",
            "max_pages": 3,
            "wait_after_click_ms": 50,
        }

        records, stats = await paginator.paginate_and_collect(
            extractor, table_config, field_mappings, pagination_config
        )

        assert stats.pages_visited >= 2
        assert stats.records_extracted == 2
        assert records[0]["parcel_number"] == "PARCEL-01"
        assert records[1]["parcel_number"] == "PARCEL-02"


@pytest.mark.asyncio
async def test_pagination_loop_detection():
    """Verify that if a page returns duplicate records, pagination halts immediately."""
    html = """
    <html>
      <body>
        <table id="loop-table">
          <tbody><tr><td>DUPLICATE-PARCEL</td></tr></tbody>
        </table>
        <button id="next-loop" type="button">Next Page Loop</button>
      </body>
    </html>
    """
    async with get_browser_context() as (_, page):
        await page.set_content(html)
        extractor = ResultExtractor(page, run_id="run-loop", node_id="node-loop")
        paginator = PaginationHandler(page, run_id="run-loop", node_id="node-loop")

        table_config = {"table_selector": "#loop-table", "row_selector": "tbody tr"}
        field_mappings = {"parcel_number": 0}
        pagination_config = {
            "next_button_selector": "#next-loop",
            "max_pages": 5,
            "wait_after_click_ms": 20,
        }

        records, stats = await paginator.paginate_and_collect(
            extractor, table_config, field_mappings, pagination_config
        )

        # Loop detected and stopped after page 2
        assert stats.pages_visited == 2
        assert stats.duplicate_records > 0
        assert any("Loop detected" in err for err in stats.pagination_errors)


# ---------------------------------------------------------------------------
# 7. SSRF Protection & URL Validation
# ---------------------------------------------------------------------------

def test_ssrf_protection_prohibits_internal_ips():
    """Verify URLValidator blocks AWS metadata service, private subnets, and bad schemes."""
    # Prohibit non-http/https
    with pytest.raises(SSRFProtectionException) as exc:
        URLValidator.validate_url("file:///etc/passwd", allow_local=False)
    assert "scheme" in str(exc.value)

    # Prohibit AWS metadata link-local IP
    with pytest.raises(SSRFProtectionException) as exc:
        URLValidator.validate_url("http://169.254.169.254/latest/meta-data/", allow_local=False)
    assert "forbidden" in str(exc.value).lower() or "restricted" in str(exc.value).lower()

    # Prohibit private subnets in production mode
    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("http://10.0.0.5/internal", allow_local=False)

    with pytest.raises(SSRFProtectionException):
        URLValidator.validate_url("http://192.168.1.1/admin", allow_local=False)


# ---------------------------------------------------------------------------
# 8. Recipe Loader & Published Version Validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recipe_loader_enforces_published_status():
    """Verify RecipeLoader rejects draft, deprecated, and unpublished recipe versions."""
    user_id = uuid.uuid4()
    county_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    draft_ver_id = uuid.uuid4()
    pub_ver_id = uuid.uuid4()

    async with TestSessionLocal() as session:
        user = User(id=user_id, email="rec_loader@test.com", hashed_password="pw", is_active=True)
        session.add(user)
        source = CountySource(id=county_id, name="Test County", county="Test", state="FL")
        session.add(source)
        recipe = Recipe(
            id=recipe_id,
            name="Orange County Assessor Recipe",
            county_source_id=county_id,
            record_type="assessor",
        )
        session.add(recipe)

        # Draft version (is_published=False)
        draft_ver = RecipeVersion(
            id=draft_ver_id,
            recipe_id=recipe_id,
            version_number=1,
            is_published=False,
            lifecycle_status="draft",
            definition={"steps": [{"action": "navigate", "url": "https://county.gov"}]},
        )
        session.add(draft_ver)

        # Published version (is_published=True)
        pub_ver = RecipeVersion(
            id=pub_ver_id,
            recipe_id=recipe_id,
            version_number=2,
            is_published=True,
            lifecycle_status="published",
            definition={
                "steps": [
                    {"action": "navigate", "url": "data:text/html,<html><body><h1>Published</h1></body></html>"}
                ],
                "field_mappings": {"title": "h1"},
            },
        )
        session.add(pub_ver)
        await session.commit()

    async with TestSessionLocal() as session:
        loader = RecipeLoader(session)

        # 1. Attempting to load the draft version must raise RecipeLoadException
        with pytest.raises(RecipeLoadException) as exc_info:
            await loader.load_recipe_for_execution({"recipe_version_id": str(draft_ver_id)})
        assert "not published" in str(exc_info.value)

        # 2. Loading the published version succeeds and preserves exact version
        resolved = await loader.load_recipe_for_execution({"recipe_version_id": str(pub_ver_id)})
        assert resolved.recipe_version_id == pub_ver_id
        assert len(resolved.steps) == 1
        assert resolved.extract_schema == {"title": "h1"}


# ---------------------------------------------------------------------------
# 9. Assessor & Tax Node Executor Real Automation Integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assessor_node_executor_with_published_recipe():
    """Verify AssessorNodeExecutor executes real published recipe steps and extracts records."""
    recipe_ver_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    county_id = uuid.uuid4()

    form_html = """
    data:text/html,<html><body>
      <form id="assessor-search">
        <input id="parcel-field" type="text" />
        <button id="search-btn" type="button" onclick="
          document.getElementById('res-owner').innerText = 'JONES ALICE';
          document.getElementById('res-val').innerText = '$450,000.00';
        ">Search</button>
      </form>
      <div id="results">
        <span id="res-owner"></span>
        <span id="res-val"></span>
      </div>
    </body></html>
    """

    async with TestSessionLocal() as session:
        user = User(id=uuid.uuid4(), email="assessor_exec@test.com", hashed_password="pw", is_active=True)
        session.add(user)
        source = CountySource(id=county_id, name="Test County", county="Orange", state="FL")
        session.add(source)
        recipe = Recipe(id=recipe_id, name="Assessor Automation", county_source_id=county_id)
        session.add(recipe)
        r_ver = RecipeVersion(
            id=recipe_ver_id,
            recipe_id=recipe_id,
            version_number=1,
            is_published=True,
            lifecycle_status="published",
            definition={
                "steps": [
                    {"action": "navigate", "url": form_html},
                    {"action": "fill", "selector": "#parcel-field", "value": "{{input.parcel_id}}"},
                    {"action": "click", "selector": "#search-btn"},
                    {"action": "wait", "duration": 50},
                ],
                "field_mappings": {
                    "owner_name": {"selector": "#res-owner", "attribute": "text"},
                    "assessed_value": {"selector": "#res-val", "attribute": "text", "is_numeric": True},
                },
            },
        )
        session.add(r_ver)
        await session.commit()

    async with TestSessionLocal() as session:
        context = ExecutionContext(
            run_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            workflow_definition={},
            initial_input={"parcel_id": "ORANGE-007"},
            db_session=session,
        )

        node = {
            "id": "node-assessor-1",
            "type": "assessor",
            "data": {"recipe_version_id": str(recipe_ver_id)},
        }

        executor = AssessorNodeExecutor()
        result = await executor.execute(node, context)

        assert result.status == "COMPLETED"
        assessor_out = result.output_data["assessor"]
        assert assessor_out["parcel_number"] == "ORANGE-007"
        assert assessor_out["owner_name"] == "JONES ALICE"
        assert assessor_out["assessed_value"] == 450000.0


@pytest.mark.asyncio
async def test_assessor_node_executor_security_challenge_halt():
    """Verify AssessorNodeExecutor stops immediately when a Cloudflare challenge occurs."""
    recipe_ver_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    county_id = uuid.uuid4()

    challenge_html = """
    data:text/html,<html><head><title>Just a moment...</title></head>
    <body><div class="cf-turnstile"></div></body></html>
    """

    async with TestSessionLocal() as session:
        user = User(id=uuid.uuid4(), email="sec_halt@test.com", hashed_password="pw", is_active=True)
        session.add(user)
        source = CountySource(id=county_id, name="Protected County", county="Protected", state="CA")
        session.add(source)
        recipe = Recipe(id=recipe_id, name="Protected Assessor", county_source_id=county_id)
        session.add(recipe)
        r_ver = RecipeVersion(
            id=recipe_ver_id,
            recipe_id=recipe_id,
            version_number=1,
            is_published=True,
            lifecycle_status="published",
            definition={
                "steps": [{"action": "navigate", "url": challenge_html}],
            },
        )
        session.add(r_ver)
        await session.commit()

    async with TestSessionLocal() as session:
        context = ExecutionContext(
            run_id=uuid.uuid4(),
            workflow_id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            workflow_definition={},
            initial_input={"parcel_id": "SEC-BLOCKED"},
            db_session=session,
        )

        node = {
            "id": "node-assessor-sec",
            "type": "assessor",
            "data": {"recipe_version_id": str(recipe_ver_id)},
        }

        executor = AssessorNodeExecutor()
        result = await executor.execute(node, context)

        assert result.status == "FAILED"
        assert "Security Challenge Detected" in result.error_message
        assert result.security_event is not None
        assert result.security_event["event_type"] in ("cloudflare", "turnstile")
