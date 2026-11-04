import asyncio
import httpx
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.workflow import Workflow
from app.core.security import create_access_token
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User))).scalars().first()
        token = create_access_token(str(user.id))
        wf = (await db.execute(select(Workflow).where(Workflow.name.ilike("%assessor%")))).scalars().first()

        print(f"Using Workflow: {wf.name} (id: {wf.id})")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Test Case: Search by Owner Name
        test_owner = "SMITH"
        print(f"\n--- Testing Search by Owner Name: '{test_owner}' ---")
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.post(
                f"http://127.0.0.1:8000/api/v1/workflows/{wf.id}/execute",
                headers=headers,
                json={"input_data": {"owner_name": test_owner, "parcel_id": "", "address": ""}},
            )
            res_json = res.json()
            run_id = res_json.get("id") or res_json.get("workflow_run_id")
            print("Run ID:", run_id)

            # Poll for completion
            for _ in range(30):
                await asyncio.sleep(2)
                r_status = await client.get(f"http://127.0.0.1:8000/api/v1/executions/{run_id}", headers=headers)
                state = r_status.json().get("status")
                print(f"Polling run {run_id}: {state}")
                if state in ("COMPLETED", "FAILED", "CANCELLED"):
                    break

            # Fetch results
            r_results = await client.get(f"http://127.0.0.1:8000/api/v1/executions/{run_id}/results", headers=headers)
            print("Run final results status:", r_results.status_code)

if __name__ == "__main__":
    asyncio.run(main())
