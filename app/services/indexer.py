from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import psutil
from arq import Retry
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.config import settings
from app.db.models import Document, Page, Structure
from app.db.session import AsyncSessionLocal, get_redis
from app.services.background import spawn

# Set env BEFORE importing pageindex_src
os.environ["OPENAI_API_BASE"] = settings.LLM_BASE_URL
os.environ["OPENAI_API_KEY"] = settings.LLM_API_KEY or "empty"

# pageindex_src uses relative imports, so import it as a package (project root on path)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pageindex_src.page_index import page_index  # noqa: E402

logger = logging.getLogger("pageserve.indexer")

CHUNK_SIZE = 20  # pages processed per chunk during text extraction


class IndexCancelled(Exception):
    """Raised internally when a document is deleted/cancelled mid-indexing."""


def _fmt_model(model: str) -> str:
    return model if model.startswith("openai/") else f"openai/{model}"


async def _is_cancelled(db: AsyncSession, redis: Redis, doc_id: str) -> bool:
    """True if the document was deleted, or an explicit cancel flag was set."""
    if await redis.get(f"cancel:{doc_id}"):
        return True
    # Fresh read (commit first so READ COMMITTED sees other tx deletes).
    await db.commit()
    return await db.scalar(select(Document.id).where(Document.id == doc_id)) is None


async def _checkpoint(db: AsyncSession, redis: Redis, doc_id: str) -> None:
    """Abort the task cleanly if the document is gone."""
    if await _is_cancelled(db, redis, doc_id):
        raise IndexCancelled(doc_id)


async def index_document_task(ctx: dict[str, Any], doc_id: str, pdf_path: str) -> None:
    """ARQ task entrypoint. Opens its own DB session for concurrency safety."""
    redis = ctx.get("redis") or await get_redis()

    # RAM guard before starting
    available_gb = psutil.virtual_memory().available / (1024**3)
    file_size_mb = Path(pdf_path).stat().st_size / (1024**2) if Path(pdf_path).exists() else 0
    estimated_gb = file_size_mb * 0.08  # rough: 100MB PDF ~ 0.8GB RAM

    if available_gb < 1.5:
        raise Retry(defer=30)
    if estimated_gb > available_gb * 0.7:
        raise Retry(
            defer=60,
            message=f"Need ~{estimated_gb:.1f}GB RAM, only {available_gb:.1f}GB free",
        )

    async with AsyncSessionLocal() as db:
        # Bail out immediately if the doc was deleted/cancelled before we started.
        if await _is_cancelled(db, redis, doc_id):
            logger.info("Skip indexing %s — already deleted/cancelled", doc_id)
            return

        await _update_status(db, doc_id, "indexing")
        await _publish(redis, doc_id, {"status": "indexing", "progress": 0})

        try:
            if not Path(pdf_path).exists():
                raise FileNotFoundError(f"PDF not found: {pdf_path}")

            # Page count
            import PyPDF2

            with open(pdf_path, "rb") as f:
                page_count = len(PyPDF2.PdfReader(f).pages)

            await _update_page_count(db, doc_id, page_count)
            await _publish(redis, doc_id, {"status": "indexing", "progress": 10})

            # Extract page text in chunks (RAM safe)
            await _extract_pages_chunked(db, doc_id, pdf_path, page_count, redis)
            await _publish(redis, doc_id, {"status": "indexing", "progress": 50})

            # Don't spend LLM tokens on a doc that was deleted while extracting.
            await _checkpoint(db, redis, doc_id)

            # Build tree with PageIndex OSS (sync fn → run in thread).
            # Node summaries cost one LLM call per node; kept ON by default (original
            # behaviour). Can be disabled via env to speed up indexing on slow LLMs.
            result = await asyncio.to_thread(
                page_index,
                pdf_path,
                model=_fmt_model(settings.LLM_MODEL),
                if_add_node_id="yes",
                if_add_node_summary=os.getenv("PAGEINDEX_NODE_SUMMARY", "yes"),
                if_add_node_text="no",  # text already lives in the pages table
                if_add_doc_description=os.getenv("PAGEINDEX_DOC_DESCRIPTION", "yes"),
            )
            await _publish(redis, doc_id, {"status": "indexing", "progress": 80})

            # The build can take minutes — re-check before persisting anything.
            await _checkpoint(db, redis, doc_id)

            # Persist structure + finalise
            await _save_structure(db, doc_id, result["structure"])
            await _update_status(
                db,
                doc_id,
                "completed",
                description=result.get("doc_description", "") or "",
            )
            await _publish(redis, doc_id, {"status": "completed", "progress": 100})

            spawn(_fire_webhook(doc_id, "document.completed"))

        except IndexCancelled:
            # Document was deleted/cancelled — discard work, no retry, no failure row.
            logger.info("Indexing cancelled for %s — discarding", doc_id)
            Path(pdf_path).unlink(missing_ok=True)
            return
        except asyncio.CancelledError:
            # ARQ cancelled the job (job_timeout). CancelledError is a BaseException,
            # so the generic handler below misses it — mark the doc failed via an
            # independent task (awaiting here would re-raise the cancellation).
            logger.warning("Indexing timed out for %s", doc_id)
            spawn(_mark_failed_bg(doc_id, "Indexing timed out (worker job_timeout exceeded)"))
            raise
        except Exception as e:  # noqa: BLE001
            # Doc deleted underneath us? Treat as cancellation, not a failure.
            if await _is_cancelled(db, redis, doc_id):
                logger.info("Indexing aborted for deleted doc %s", doc_id)
                return
            # Real, permanent error → mark failed and STOP (no automatic retry storm).
            error_msg = str(e)[:500]
            logger.warning("Indexing failed for %s: %s", doc_id, error_msg)
            await _update_status(db, doc_id, "failed", error_msg=error_msg)
            await _publish(redis, doc_id, {"status": "failed", "error": error_msg})
            spawn(_fire_webhook(doc_id, "document.failed"))
            return  # do NOT raise → ARQ won't retry; user can reindex manually
        finally:
            gc.collect()


# Helpers


async def _publish(redis: Redis, doc_id: str, payload: dict[str, Any]) -> None:
    await redis.publish(f"index:{doc_id}", json.dumps(payload))


async def _extract_pages_chunked(
    db: AsyncSession, doc_id: str, pdf_path: str, page_count: int, redis: Redis
) -> None:
    """Extract per-page text in chunks, opening/closing the PDF each chunk to free RAM."""
    import pymupdf  # PyMuPDF — faster text extraction than PyPDF2

    for chunk_start in range(0, page_count, CHUNK_SIZE):
        chunk_end = min(chunk_start + CHUNK_SIZE, page_count)
        pages_data = []

        pdf = pymupdf.open(pdf_path)
        try:
            for page_num in range(chunk_start, chunk_end):
                text = pdf[page_num].get_text("text")
                pages_data.append(
                    {"doc_id": doc_id, "page_num": page_num + 1, "content": text or ""}
                )
        finally:
            pdf.close()

        # Bulk insert; idempotent so retries don't double-insert
        await db.execute(insert(Page).values(pages_data).on_conflict_do_nothing())
        await db.commit()

        del pages_data
        gc.collect()

        progress = 10 + int((chunk_end / max(page_count, 1)) * 40)  # 10% -> 50%
        await _publish(redis, doc_id, {"status": "indexing", "progress": progress})


async def _save_structure(db: AsyncSession, doc_id: str, tree: list[dict[str, Any]]) -> None:
    await db.execute(
        insert(Structure)
        .values(doc_id=doc_id, tree=tree)
        .on_conflict_do_update(
            index_elements=["doc_id"], set_={"tree": tree, "updated_at": func.now()}
        )
    )
    await db.commit()


async def _update_status(db: AsyncSession, doc_id: str, status: str, **kwargs: Any) -> None:
    values = {"status": status, "updated_at": func.now()}
    values.update(kwargs)
    await db.execute(update(Document).where(Document.id == doc_id).values(**values))
    await db.commit()


async def _update_page_count(db: AsyncSession, doc_id: str, page_count: int) -> None:
    await db.execute(update(Document).where(Document.id == doc_id).values(page_count=page_count))
    await db.commit()


async def _fire_webhook(doc_id: str, event: str) -> None:
    from app.services.webhook import fire_event_standalone

    await fire_event_standalone(doc_id, event)


async def _mark_failed_bg(doc_id: str, msg: str) -> None:
    """Mark a doc failed from outside the (cancelled) task — its own session/loop work."""
    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Document)
            .where(Document.id == doc_id, Document.status == "indexing")
            .values(status="failed", error_msg=msg, updated_at=func.now())
        )
        await db.commit()
    await _publish(redis, doc_id, {"status": "failed", "error": msg})
    spawn(_fire_webhook(doc_id, "document.failed"))


async def recover_orphaned_documents() -> None:
    """Called on worker startup. Any doc left in 'indexing' belongs to a job that
    died mid-run (worker crash/restart) — mark it failed so it never hangs forever.
    We do NOT auto-requeue, to avoid surprise LLM cost; the user reindexes on demand.

    Best-effort: on a fresh DB the worker may start before the API has run
    migrations, so the table might not exist yet — never let that crash startup.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(Document)
                .where(Document.status == "indexing")
                .values(
                    status="failed",
                    error_msg="Indexing was interrupted (worker restart). Please reindex.",
                    updated_at=func.now(),
                )
            )
            if result.rowcount:
                await db.commit()
                logger.warning(
                    "Reset %d orphaned 'indexing' document(s) to failed", result.rowcount
                )
    except Exception as e:  # noqa: BLE001
        logger.info("Orphan recovery skipped (schema not ready yet?): %s", e)
