import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("backend"))

from app.db.session import AsyncSessionLocal
from app.models.execution import WorkflowRun, NodeRun
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(WorkflowRun).where(WorkflowRun.id == 'f9ae5014-a707-4246-ba28-76485667e08c'))
        run = res.scalar_one_or_none()
        if run:
            print("Status:", run.status)
            print("Metadata:", run.execution_metadata)
            nr_res = await session.execute(select(NodeRun).where(NodeRun.workflow_run_id == run.id))
            for nr in nr_res.scalars():
                print(f"Node {nr.node_id} ({nr.node_type}): {nr.output_data}")

if __name__ == "__main__":
    asyncio.run(main())

