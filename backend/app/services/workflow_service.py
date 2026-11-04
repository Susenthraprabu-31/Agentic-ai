"""Workflow service — CRUD, versioning, and validation for Workflow resources."""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.execution import WorkflowRun
from app.models.workflow import Workflow, WorkflowVersion
from app.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpdateRequest,
    WorkflowValidationResponse,
    WorkflowVersionCreateRequest,
    WorkflowVersionResponse,
)
from app.services.workflow_validator import validate_workflow_graph

logger = get_logger(__name__)

DEFAULT_WORKFLOW_DEFINITION: dict[str, Any] = {
    "nodes": [
        {
            "id": "node-input",
            "type": "input",
            "position": {"x": 100, "y": 200},
            "data": {
                "label": "Property Search Input",
                "required_params": ["parcel_id"],
                "optional_params": ["address", "owner_name"],
            },
        },
        {
            "id": "node-assessor",
            "type": "assessor",
            "position": {"x": 420, "y": 100},
            "data": {
                "label": "County Assessor Search",
                "allow_failure": True,
            },
        },
        {
            "id": "node-tax",
            "type": "tax",
            "position": {"x": 420, "y": 300},
            "data": {
                "label": "County Tax Search",
                "allow_failure": True,
            },
        },
        {
            "id": "node-normalize",
            "type": "normalize",
            "position": {"x": 740, "y": 200},
            "data": {
                "label": "Data Harmonization",
                "mappings": {
                    "parcel_id": "assessor.parcel_id || tax.parcel_id || input.parcel_id",
                    "property_address": "assessor.address",
                    "assessed_value": "assessor.assessed_value",
                    "tax_delinquent": "tax.is_delinquent",
                },
                "support_partial_success": True,
            },
        },
        {
            "id": "node-output",
            "type": "output",
            "position": {"x": 1020, "y": 200},
            "data": {
                "label": "Structured Title Output",
                "output_format": "json",
            },
        },
    ],
    "edges": [
        {"id": "e-in-assessor", "source": "node-input", "target": "node-assessor"},
        {"id": "e-in-tax", "source": "node-input", "target": "node-tax"},
        {"id": "e-assessor-norm", "source": "node-assessor", "target": "node-normalize"},
        {"id": "e-tax-norm", "source": "node-tax", "target": "node-normalize"},
        {"id": "e-norm-output", "source": "node-normalize", "target": "node-output"},
    ],
    "execution_config": {
        "mode": "parallel_branches",
        "allow_partial_success": True,
        "timeout_seconds": 120,
    },
}


class WorkflowService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _to_workflow_response(self, wf: Workflow) -> WorkflowResponse:
        current_ver = next((v for v in wf.versions if v.is_current), None)
        if not current_ver and wf.versions:
            current_ver = wf.versions[0]

        version_responses = [
            WorkflowVersionResponse(
                id=v.id,
                workflow_id=v.workflow_id,
                version_number=v.version_number,
                definition=v.definition,
                changelog=v.changelog,
                is_current=v.is_current,
                created_at=v.created_at,
            )
            for v in wf.versions
        ]

        return WorkflowResponse(
            id=wf.id,
            name=wf.name,
            description=wf.description,
            owner_id=wf.owner_id,
            is_active=wf.is_active,
            county_source_id=wf.county_source_id,
            current_version_id=current_ver.id if current_ver else None,
            current_version_number=current_ver.version_number if current_ver else None,
            created_at=wf.created_at,
            updated_at=wf.updated_at,
            versions=version_responses,
        )

    async def list_workflows(
        self,
        search: str | None = None,
        is_active: bool | None = None,
        county_source_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> WorkflowListResponse:
        query = select(Workflow).options(selectinload(Workflow.versions))
        count_query = select(func.count(Workflow.id))

        if search:
            query = query.where(Workflow.name.ilike(f"%{search}%"))
            count_query = count_query.where(Workflow.name.ilike(f"%{search}%"))

        if is_active is not None:
            query = query.where(Workflow.is_active == is_active)
            count_query = count_query.where(Workflow.is_active == is_active)

        if county_source_id is not None:
            query = query.where(Workflow.county_source_id == county_source_id)
            count_query = count_query.where(Workflow.county_source_id == county_source_id)

        total_res = await self._db.execute(count_query)
        total = total_res.scalar() or 0

        query = query.order_by(Workflow.created_at.desc()).offset(skip).limit(limit)
        res = await self._db.execute(query)
        workflows = res.scalars().all()

        return WorkflowListResponse(
            items=[self._to_workflow_response(wf) for wf in workflows],
            total=total,
        )

    async def create_workflow(
        self, payload: WorkflowCreateRequest, owner_id: uuid.UUID
    ) -> WorkflowResponse:
        initial_def = payload.initial_definition or DEFAULT_WORKFLOW_DEFINITION

        # Validate graph topology
        val = validate_workflow_graph(initial_def)
        if not val["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid workflow definition: {'; '.join(val['errors'])}",
            )

        workflow = Workflow(
            name=payload.name,
            description=payload.description,
            owner_id=owner_id,
            is_active=True,
            county_source_id=payload.county_source_id,
        )
        self._db.add(workflow)
        await self._db.flush()

        initial_version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=1,
            definition=initial_def,
            changelog="Initial workflow version created",
            is_current=True,
        )
        self._db.add(initial_version)
        await self._db.commit()

        return await self.get_workflow(workflow.id)

    async def get_workflow(self, workflow_id: uuid.UUID) -> WorkflowResponse:
        query = (
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        res = await self._db.execute(query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )
        return self._to_workflow_response(workflow)

    async def update_workflow(
        self,
        workflow_id: uuid.UUID,
        payload: WorkflowUpdateRequest,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WorkflowResponse:
        query = (
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        res = await self._db.execute(query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )

        if not is_superuser and workflow.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this workflow.",
            )

        if payload.name is not None:
            workflow.name = payload.name
        if payload.description is not None:
            workflow.description = payload.description
        if payload.is_active is not None:
            workflow.is_active = payload.is_active
        if payload.county_source_id is not None:
            workflow.county_source_id = payload.county_source_id

        await self._db.commit()
        return self._to_workflow_response(workflow)

    async def delete_workflow(
        self,
        workflow_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> None:
        query = select(Workflow).where(Workflow.id == workflow_id)
        res = await self._db.execute(query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )

        if not is_superuser and workflow.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete this workflow.",
            )

        # Cleanly delete all workflow_runs associated with this workflow or its versions first
        # to respect foreign key constraints on workflow_runs
        version_ids_query = select(WorkflowVersion.id).where(WorkflowVersion.workflow_id == workflow_id)
        await self._db.execute(
            delete(WorkflowRun).where(
                (WorkflowRun.workflow_id == workflow_id)
                | (WorkflowRun.workflow_version_id.in_(version_ids_query))
            )
        )

        await self._db.delete(workflow)
        await self._db.commit()

    async def create_workflow_version(
        self,
        workflow_id: uuid.UUID,
        payload: WorkflowVersionCreateRequest,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WorkflowVersionResponse:
        query = (
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        res = await self._db.execute(query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )

        if not is_superuser and workflow.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to add versions to this workflow.",
            )

        # Validate graph
        val = validate_workflow_graph(payload.definition)
        if not val["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid workflow graph definition: {'; '.join(val['errors'])}",
            )

        next_ver_num = (
            max((v.version_number for v in workflow.versions), default=0) + 1
        )

        # If marking this version as current, unmark previous current
        if payload.is_current:
            for v in workflow.versions:
                v.is_current = False

        new_version = WorkflowVersion(
            workflow_id=workflow.id,
            version_number=next_ver_num,
            definition=payload.definition,
            changelog=payload.changelog,
            is_current=payload.is_current,
        )
        self._db.add(new_version)
        await self._db.commit()
        await self._db.refresh(new_version)

        return WorkflowVersionResponse(
            id=new_version.id,
            workflow_id=new_version.workflow_id,
            version_number=new_version.version_number,
            definition=new_version.definition,
            changelog=new_version.changelog,
            is_current=new_version.is_current,
            created_at=new_version.created_at,
        )

    async def list_workflow_versions(
        self, workflow_id: uuid.UUID
    ) -> list[WorkflowVersionResponse]:
        query = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version_number.desc())
        )
        res = await self._db.execute(query)
        versions = res.scalars().all()
        return [
            WorkflowVersionResponse(
                id=v.id,
                workflow_id=v.workflow_id,
                version_number=v.version_number,
                definition=v.definition,
                changelog=v.changelog,
                is_current=v.is_current,
                created_at=v.created_at,
            )
            for v in versions
        ]

    async def get_workflow_version(
        self, workflow_id: uuid.UUID, version_id: uuid.UUID
    ) -> WorkflowVersionResponse:
        query = select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id,
            WorkflowVersion.id == version_id,
        )
        res = await self._db.execute(query)
        ver = res.scalar_one_or_none()
        if not ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow version {version_id} not found for workflow {workflow_id}.",
            )
        return WorkflowVersionResponse(
            id=ver.id,
            workflow_id=ver.workflow_id,
            version_number=ver.version_number,
            definition=ver.definition,
            changelog=ver.changelog,
            is_current=ver.is_current,
            created_at=ver.created_at,
        )

    async def set_current_version(
        self,
        workflow_id: uuid.UUID,
        version_id: uuid.UUID,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> WorkflowVersionResponse:
        query = (
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        res = await self._db.execute(query)
        workflow = res.scalar_one_or_none()
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID {workflow_id} not found.",
            )

        if not is_superuser and workflow.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this workflow.",
            )

        target_ver = next((v for v in workflow.versions if v.id == version_id), None)
        if not target_ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow version {version_id} not found.",
            )

        for v in workflow.versions:
            v.is_current = v.id == target_ver.id

        await self._db.commit()
        await self._db.refresh(target_ver)

        return WorkflowVersionResponse(
            id=target_ver.id,
            workflow_id=target_ver.workflow_id,
            version_number=target_ver.version_number,
            definition=target_ver.definition,
            changelog=target_ver.changelog,
            is_current=target_ver.is_current,
            created_at=target_ver.created_at,
        )

    def validate_workflow_definition(
        self, definition: dict[str, Any]
    ) -> WorkflowValidationResponse:
        val = validate_workflow_graph(definition)
        return WorkflowValidationResponse(
            is_valid=val["is_valid"],
            errors=val["errors"],
            warnings=val["warnings"],
            execution_order=val["execution_order"],
            node_count=val["node_count"],
            edge_count=val["edge_count"],
            supports_assessor=val["supports_assessor"],
            supports_tax=val["supports_tax"],
            supports_partial_success=val["supports_partial_success"],
        )
