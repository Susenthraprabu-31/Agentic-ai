"""Seed script for Collier County Property Appraiser recipe and workflow binding."""

import asyncio
import sys
import uuid
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.db.session import AsyncSessionLocal
from app.models import CountySource, Recipe, RecipeVersion, User, Workflow, WorkflowVersion


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # 1. Fetch Collier County Source
        cs_stmt = select(CountySource).where(CountySource.county == "Collier")
        cs = (await session.execute(cs_stmt)).scalar_one_or_none()
        if not cs:
            print("Collier CountySource not found!")
            return

        # 2. Fetch User
        user_stmt = select(User).limit(1)
        user = (await session.execute(user_stmt)).scalar_one_or_none()
        user_id = user.id if user else None

        # 3. Check if Recipe already exists
        r_stmt = select(Recipe).where(Recipe.county_source_id == cs.id, Recipe.record_type == "assessor")
        recipe = (await session.execute(r_stmt)).scalar_one_or_none()

        recipe_definition = {
            "steps": [
                {
                    "action": "navigate",
                    "url": "https://www.collierappraiser.com/Main_Search/search_rp.html",
                    "description": "Navigate to Collier County search portal",
                    "timeout_ms": 25000,
                },
                {
                    "action": "wait_for_selector",
                    "selector": "#OwnerLine1",
                    "description": "Wait for search form to be visible",
                    "timeout_ms": 15000,
                },
                {
                    "action": "fill",
                    "selector": "#OwnerLine1",
                    "value": "{{input.owner_name}}",
                    "description": "Enter owner name into search input",
                    "timeout_ms": 10000,
                },
                {
                    "action": "click",
                    "selector": "#SearchName1",
                    "description": "Click Search button",
                    "timeout_ms": 10000,
                },
                {
                    "action": "wait_for_selector",
                    "selector": "#flex1 tr.jqgrow, #flex1 tr[id]",
                    "description": "Wait for search results rows to populate",
                    "timeout_ms": 25000,
                },
            ],
            "field_mappings": {
                "parcel_number": 0,
                "owner_name": 2,
                "property_address": 3,
                "assessed_value": {"column_index": 10, "is_numeric": True},
            },
            "table_settings": {
                "table_selector": "#flex1",
                "row_selector": "tr.jqgrow",
            },
        }

        if not recipe:
            recipe_id = uuid.uuid4()
            recipe = Recipe(
                id=recipe_id,
                name="Collier County Assessor Property Search",
                record_type="assessor",
                county_source_id=cs.id,
                status="published",
                description="Approved recipe for Collier County Property Appraiser (Naples, FL)",
                created_by=user_id,
            )
            session.add(recipe)
            await session.flush()
            print(f"Created Recipe: {recipe.id} - {recipe.name}")

            recipe_version = RecipeVersion(
                id=uuid.uuid4(),
                recipe_id=recipe.id,
                version_number=1,
                definition=recipe_definition,
                lifecycle_status="published",
                is_current=True,
                is_published=True,
                validation_status="passed",
                notes="Initial approved recipe for live property search",
                created_by=user_id,
            )
            session.add(recipe_version)
            await session.flush()
            print(f"Created RecipeVersion: {recipe_version.id} (v1)")
        else:
            # Update existing recipe version definition
            rv_stmt = (
                select(RecipeVersion)
                .where(RecipeVersion.recipe_id == recipe.id)
                .order_by(RecipeVersion.version_number.desc())
            )
            recipe_version = (await session.execute(rv_stmt)).scalars().first()
            if recipe_version:
                recipe_version.definition = recipe_definition
                recipe_version.is_published = True
                recipe_version.lifecycle_status = "published"
                flag_modified(recipe_version, "definition")
                print(f"Updated RecipeVersion: {recipe_version.id}")

        # 4. Bind to Assessor Workflow
        wf_stmt = select(Workflow).where(Workflow.name == "Assessor Workflow")
        wf = (await session.execute(wf_stmt)).scalar_one_or_none()
        if wf:
            wf.county_source_id = cs.id
            wv_stmt = (
                select(WorkflowVersion)
                .where(WorkflowVersion.workflow_id == wf.id)
                .order_by(WorkflowVersion.version_number.desc())
            )
            wv = (await session.execute(wv_stmt)).scalars().first()
            if wv and wv.definition:
                nodes = wv.definition.get("nodes", [])
                for n in nodes:
                    if n.get("type") == "assessor":
                        n_data = n.setdefault("data", {})
                        n_data["county_source_id"] = str(cs.id)
                        n_data["recipe_id"] = str(recipe.id)
                        n_data["recipe_version_id"] = str(recipe_version.id)
                flag_modified(wv, "definition")
                print(f"Updated WorkflowVersion {wv.id} for Workflow {wf.id}")

        await session.commit()
        print("Database commit successful.")


if __name__ == "__main__":
    asyncio.run(seed())
