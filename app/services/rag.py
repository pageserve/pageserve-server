from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select

from app.config import settings
from app.db.models import Page, Structure
from app.db.session import AsyncSessionLocal
from app.services.cache import (
    cache_query_result,
    get_cached_query,
    query_cache_key,
    retrieve_cache_key,
)

# Set env BEFORE importing pageindex_src deps (litellm)
os.environ["OPENAI_API_BASE"] = settings.LLM_BASE_URL
os.environ["OPENAI_API_KEY"] = settings.LLM_API_KEY or "empty"

_PAGEINDEX_SRC = str(Path(__file__).resolve().parent.parent.parent / "pageindex_src")
if _PAGEINDEX_SRC not in sys.path:
    sys.path.insert(0, _PAGEINDEX_SRC)

import litellm  # noqa: E402

MAX_ITERATIONS = 10

# Disable Qwen "thinking" unless LLM_DISABLE_THINKING is turned off.
_EXTRA_BODY = (
    {"chat_template_kwargs": {"enable_thinking": False}}
    if os.getenv("LLM_DISABLE_THINKING", "true").lower() in ("1", "true", "yes")
    else {}
)

SYSTEM_PROMPT = """Bạn là trợ lý phân tích tài liệu chính xác.

WORKFLOW:
1. Gọi get_document_structure() để xác định section/trang liên quan
2. Gọi get_page_content() với range hẹp nhất (không fetch toàn bộ doc)
3. Trả lời dựa trên nội dung trang. Luôn ghi rõ số trang nguồn.

CITATION FORMAT: [[doc_id:page_number]]
Ví dụ: "Lương thử việc tối thiểu 85% [[luat-lao-dong:24]]"

Không fabricate, chỉ dùng thông tin từ tool results."""


def _model() -> str:
    name = settings.LLM_RETRIEVE_MODEL or settings.LLM_MODEL
    return name if name.startswith("openai/") else f"openai/{name}"


def _make_tools(doc_ids: list[str]) -> list[dict[str, Any]]:
    ids_desc = ", ".join(doc_ids)
    return [
        {
            "type": "function",
            "function": {
                "name": "get_document_structure",
                "description": (
                    "Lấy tree structure (mục lục) của document để xác định section liên quan. "
                    "Gọi trước khi get_page_content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": f"Một trong: {ids_desc}",
                        }
                    },
                    "required": ["doc_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_page_content",
                "description": (
                    "Lấy text content của trang cụ thể. Dùng range hẹp nhất có thể. "
                    "Instant — không gọi LLM."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_id": {
                            "type": "string",
                            "description": f"Một trong: {ids_desc}",
                        },
                        "pages": {
                            "type": "string",
                            "description": "Format: '5' | '5-7' | '3,8,12'",
                        },
                    },
                    "required": ["doc_id", "pages"],
                },
            },
        },
    ]


# Tool implementations. Each opens a short-lived session so we never hold a DB
# connection across the (slow) LLM calls in the agent loop — see audit H1.
async def _get_structure(doc_id: str, redis: Redis) -> str:
    """Return trimmed tree JSON (title + page range only) — cached for 1 hour."""
    cached = await redis.get(f"structure:{doc_id}")
    if cached:
        return cached

    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(Structure).where(Structure.doc_id == doc_id))
    if not row:
        return json.dumps({"error": f"Document {doc_id} chưa có structure"})

    def _trim(nodes):
        return [
            {
                "title": n.get("title"),
                "node_id": n.get("node_id"),
                "start_index": n.get("start_index"),
                "end_index": n.get("end_index"),
                "nodes": _trim(n.get("nodes", [])),
            }
            for n in nodes
        ]

    trimmed = json.dumps(_trim(row.tree), ensure_ascii=False)
    await redis.setex(f"structure:{doc_id}", 3600, trimmed)
    return trimmed


def _parse_pages(pages_str: str) -> list[int]:
    """Parse '5-7' -> [5,6,7], '3,8' -> [3,8], '5' -> [5]."""
    result: list[int] = []
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            result.extend(range(int(start), int(end) + 1))
        else:
            result.append(int(part))
    return result


async def _get_pages(doc_id: str, pages_str: str) -> tuple[str, dict[int, str]]:
    """Fetch only the requested pages. Returns (json_string, {page_num: content})."""
    page_nums = _parse_pages(pages_str)
    async with AsyncSessionLocal() as db:
        rows = list(
            await db.scalars(
                select(Page)
                .where(Page.doc_id == doc_id, Page.page_num.in_(page_nums))
                .order_by(Page.page_num)
            )
        )
    fetched: dict[int, str] = {}
    pages = []
    for row in rows:
        fetched[row.page_num] = row.content
        pages.append({"page": row.page_num, "content": row.content})
    return json.dumps(pages, ensure_ascii=False), fetched


def _build_sources(
    all_fetched: dict[str, dict[int, str]], all_refs: dict[str, list[int]]
) -> list[dict[str, Any]]:
    sources = []
    for doc_id, page_nums in all_refs.items():
        page_nums_sorted = sorted(set(page_nums))
        sources.append(
            {
                "doc_id": doc_id,
                "page_refs": page_nums_sorted,
                "raw_pages": [
                    {"page": p, "content": all_fetched.get(doc_id, {}).get(p, "")}
                    for p in page_nums_sorted
                ],
            }
        )
    return sources


async def _run_tool(
    name: str,
    args: dict[str, Any],
    doc_ids: list[str],
    redis: Redis,
    all_fetched: dict,
    all_refs: dict,
) -> str:
    """Execute one tool call and accumulate fetched pages/refs."""
    doc_id = args.get("doc_id", doc_ids[0])
    if name == "get_document_structure":
        return await _get_structure(doc_id, redis)
    if name == "get_page_content":
        pages_str = args.get("pages", "1")
        result, fetched = await _get_pages(doc_id, pages_str)
        all_fetched.setdefault(doc_id, {}).update(fetched)
        refs = all_refs.setdefault(doc_id, [])
        for p in _parse_pages(pages_str):
            if p not in refs:
                refs.append(p)
        return result
    return json.dumps({"error": f"Unknown tool: {name}"})


# Answer mode
async def run_query(doc_ids: list[str], question: str, redis: Redis) -> dict[str, Any]:
    """RAG query with agent loop. Returns {answer, sources, elapsed_ms, cached}."""
    start_ms = int(time.time() * 1000)
    cache_key = query_cache_key(doc_ids, question)
    cached = await get_cached_query(redis, cache_key)
    if cached:
        return {
            **cached,
            "cached": True,
            "elapsed_ms": int(time.time() * 1000) - start_ms,
        }

    tools = _make_tools(doc_ids)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    all_fetched: dict[str, dict[int, str]] = {}
    all_refs: dict[str, list[int]] = {}
    answer = "Đã vượt quá số lượt reasoning tối đa."

    for _ in range(MAX_ITERATIONS):
        resp = await litellm.acompletion(
            model=_model(),
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0,
            extra_body=_EXTRA_BODY,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        if resp.choices[0].finish_reason == "stop" or not msg.tool_calls:
            answer = msg.content or ""
            break

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            content = await _run_tool(
                tc.function.name, args, doc_ids, redis, all_fetched, all_refs
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

    result = {
        "answer": answer,
        "sources": _build_sources(all_fetched, all_refs),
        "elapsed_ms": int(time.time() * 1000) - start_ms,
        "cached": False,
    }
    await cache_query_result(redis, cache_key, result, doc_ids)
    return result


# Retrieve mode — return the FULL original content of relevant sections,
# without synthesizing an answer (1 LLM call/doc, cheaper than answer mode).
_RETRIEVE_SYSTEM = (
    "Xác định các sections trong document tree liên quan nhất đến câu hỏi. "
    "Trả về JSON array: [{node_id, title, start_index, end_index}], sắp xếp theo độ liên quan giảm dần. "
    "Chỉ trả JSON, không giải thích."
)


async def run_retrieve(
    doc_ids: list[str],
    question: str,
    redis: Redis,
    max_sections: int = 6,
    max_pages_per_section: int = 4,
) -> dict[str, Any]:
    """Return relevant sections' full page content (no answer synthesis)."""
    start_ms = int(time.time() * 1000)
    cache_key = retrieve_cache_key(doc_ids, question)
    cached = await get_cached_query(redis, cache_key)
    if cached:
        return {**cached, "cached": True, "elapsed_ms": int(time.time() * 1000) - start_ms}

    results = []
    for doc_id in doc_ids:
        structure_json = await _get_structure(doc_id, redis)
        resp = await litellm.acompletion(
            model=_model(),
            messages=[
                {"role": "system", "content": _RETRIEVE_SYSTEM},
                {"role": "user", "content": f"Tree:\n{structure_json}\n\nCâu hỏi: {question}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            extra_body=_EXTRA_BODY,
        )
        try:
            nodes = json.loads(resp.choices[0].message.content)
            if isinstance(nodes, dict):
                nodes = nodes.get("nodes", nodes.get("sections", []))
        except Exception:  # noqa: BLE001
            nodes = []

        sections = []
        for node in nodes[:max_sections]:
            start = int(node.get("start_index", 1))
            end = int(node.get("end_index", start))
            end = max(start, min(end, start + max_pages_per_section - 1))  # cap span
            _, fetched = await _get_pages(doc_id, f"{start}-{end}")
            pages = [{"page": p, "content": c} for p, c in sorted(fetched.items())]
            if pages:
                sections.append(
                    {
                        "title": node.get("title", ""),
                        "node_id": node.get("node_id"),
                        "page_start": start,
                        "page_end": end,
                        "pages": pages,
                    }
                )
        if sections:
            results.append({"doc_id": doc_id, "sections": sections})

    result = {
        "results": results,
        "elapsed_ms": int(time.time() * 1000) - start_ms,
        "cached": False,
    }
    await cache_query_result(redis, cache_key, result, doc_ids)
    return result


# Streaming (SSE)


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


async def stream_query(
    doc_ids: list[str], question: str, mode: str, redis: Redis
) -> AsyncIterator[str]:
    """
    Async generator of SSE lines. Emits tool_start/tool_done as the agent works,
    then token + sources + done. Search mode emits a single done with results.
    """
    if mode == "search":
        retr = await run_retrieve(doc_ids, question, redis)
        yield _sse("done", {"results": retr["results"]})
        return

    cache_key = query_cache_key(doc_ids, question)
    cached = await get_cached_query(redis, cache_key)
    if cached:
        yield _sse("token", {"content": cached["answer"]})
        yield _sse("sources", {"sources": cached["sources"]})
        yield _sse("done", {"cached": True})
        return

    start_ms = int(time.time() * 1000)
    tools = _make_tools(doc_ids)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    all_fetched: dict[str, dict[int, str]] = {}
    all_refs: dict[str, list[int]] = {}
    answer = "Đã vượt quá số lượt reasoning tối đa."

    try:
        for _ in range(MAX_ITERATIONS):
            # Stream each iteration so the final answer arrives token-by-token.
            stream = await litellm.acompletion(
                model=_model(),
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
                stream=True,
                extra_body=_EXTRA_BODY,
            )
            content_acc = ""
            tool_acc: dict[int, dict[str, Any]] = {}  # index -> {id, name, args}
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    content_acc += delta.content
                    yield _sse("token", {"content": delta.content})
                for tcd in getattr(delta, "tool_calls", None) or []:
                    idx = tcd.index if tcd.index is not None else 0
                    slot = tool_acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                    if tcd.id:
                        slot["id"] = tcd.id
                    fn = getattr(tcd, "function", None)
                    if fn is not None:
                        if fn.name:
                            slot["name"] = fn.name
                        if fn.arguments:
                            slot["args"] += fn.arguments

            # No tool calls this turn → the streamed content IS the final answer.
            if not tool_acc:
                answer = content_acc
                break

            # Reconstruct the assistant turn that requested tools, then run them.
            messages.append(
                {
                    "role": "assistant",
                    "content": content_acc or None,
                    "tool_calls": [
                        {
                            "id": s["id"] or f"tc_{i}",
                            "type": "function",
                            "function": {"name": s["name"], "arguments": s["args"] or "{}"},
                        }
                        for i, s in sorted(tool_acc.items())
                    ],
                }
            )
            for i, s in sorted(tool_acc.items()):
                tc_id = s["id"] or f"tc_{i}"
                try:
                    args = json.loads(s["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield _sse("tool_start", {"id": tc_id, "name": s["name"], "args": args})
                t0 = time.time()
                content = await _run_tool(s["name"], args, doc_ids, redis, all_fetched, all_refs)
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": content})
                yield _sse("tool_done", {"id": tc_id, "elapsed": round(time.time() - t0, 2)})
    except Exception as e:  # noqa: BLE001
        yield _sse("error", {"message": str(e)})
        return

    sources = _build_sources(all_fetched, all_refs)
    yield _sse("sources", {"sources": sources})

    result = {
        "answer": answer,
        "sources": sources,
        "elapsed_ms": int(time.time() * 1000) - start_ms,
        "cached": False,
    }
    await cache_query_result(redis, cache_key, result, doc_ids)
    yield _sse("done", {"cached": False})
