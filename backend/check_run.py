import asyncio
import uuid
from app.db.session import AsyncSessionLocal
from app.models.execution import WorkflowRun, NodeRun
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(WorkflowRun).where(WorkflowRun.id == uuid.UUID('657b2c3a-11fc-43d2-a497-a7503b892550')))
        run = res.scalar_one_or_none()
        if run:
            print("RUN:", run.id, run.status, run.error_message, run.workflow_id, run.input_data)
            nres = await db.execute(select(NodeRun).where(NodeRun.workflow_run_id == run.id))
            for n in nres.scalars().all():
                print("  NODE:", n.node_id, n.node_type, n.status, n.error_message, n.input_data)
        else:
            print("Run not found")

if __name__ == "__main__":
    asyncio.run(main())
