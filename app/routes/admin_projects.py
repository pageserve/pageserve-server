from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin, require_project_access
from app.db.models import ApiKey, Document, Project, ProjectMember, User
from app.db.session import get_db
from app.schemas import AddMemberRequest, CreateProjectRequest, UpdateProjectRequest

router = APIRouter(prefix="/admin", tags=["admin:projects"])


def _project_dict(p: Project) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "slug": p.slug,
        "description": p.description,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("/projects")
async def list_projects(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    projects = list(await db.scalars(select(Project).order_by(Project.created_at.desc())))

    # Aggregate counts in 3 grouped queries instead of 3 per project (audit H3).
    async def _counts(model) -> dict[Any, int]:
        rows = await db.execute(
            select(model.project_id, func.count()).group_by(model.project_id)
        )
        return {pid: count for pid, count in rows.all()}

    doc_counts = await _counts(Document)
    key_counts = await _counts(ApiKey)
    member_counts = await _counts(ProjectMember)

    return [
        {
            **_project_dict(p),
            "doc_count": doc_counts.get(p.id, 0),
            "key_count": key_counts.get(p.id, 0),
            "member_count": member_counts.get(p.id, 0),
        }
        for p in projects
    ]


@router.post("/projects", status_code=201)
async def create_project(
    req: CreateProjectRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    existing = await db.scalar(select(Project).where(Project.slug == req.slug))
    if existing:
        raise HTTPException(409, "Slug đã tồn tại")
    project = Project(name=req.name, slug=req.slug, description=req.description)
    db.add(project)
    await db.commit()
    return _project_dict(project)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    ctx=Depends(require_project_access()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _, project = ctx
    members = await db.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id))
    doc_count = await db.scalar(
        select(func.count()).select_from(Document).where(Document.project_id == project_id)
    )
    return {
        **_project_dict(project),
        "doc_count": doc_count,
        "members": [{"user_id": str(m.user_id), "role": m.role} for m in members],
    }


@router.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    req: UpdateProjectRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if req.name is not None:
        project.name = req.name
    if req.description is not None:
        project.description = req.description
    project.updated_at = datetime.now(UTC)
    await db.commit()
    return _project_dict(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.delete(project)  # cascades to docs/keys/structure/pages/webhooks
    await db.commit()


@router.post("/projects/{project_id}/members", status_code=201)
async def add_member(
    project_id: str,
    req: AddMemberRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    existing = await db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == req.user_id
        )
    )
    if existing:
        existing.role = req.role
    else:
        db.add(ProjectMember(project_id=project_id, user_id=req.user_id, role=req.role))
    await db.commit()
    return {"project_id": project_id, "user_id": req.user_id, "role": req.role}


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: str,
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    await db.commit()
