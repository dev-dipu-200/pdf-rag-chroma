import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from ollama import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user, get_optional_user, get_user_chat_session
from app.database import AsyncSessionLocal, get_db
from app.dependencies import (
    EmbeddingInitializationError,
    get_llm,
    get_vectorstore,
    shared_collection_name,
)
from app.models import AnonymousQueryUsage, ChatMessage, ChatSession, PdfDocument, User
from app.schemas import (
    ChatMessageResponse,
    ChatSessionResponse,
    QueryRequest,
    QueryResponse,
    StatusResponse,
)
from app.services.rag import build_answer_chain, format_docs

router = APIRouter(prefix="/query", tags=["query"])
logger = logging.getLogger(__name__)
ANONYMOUS_QUERY_LIMIT = 3


def _serialize_session(session: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _serialize_message(message: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        sources=message.sources or [],
        created_at=message.created_at,
    )


async def _ensure_indexed_documents(db: AsyncSession) -> None:
    result = await db.execute(
        select(PdfDocument.id)
        .filter(PdfDocument.status == "indexed")
        .limit(1)
    )
    has_docs = result.scalar_one_or_none()
    if has_docs is None:
        raise HTTPException(
            status_code=400,
            detail="No indexed PDFs found for this user. Upload PDFs and wait for indexing to finish.",
        )


async def _get_docs(question: str, top_k: int):
    vectorstore = get_vectorstore(shared_collection_name())
    return await vectorstore.asimilarity_search(question, k=max(top_k * 2, top_k + 2))


async def _get_sources_and_context(question: str, top_k: int) -> tuple[list, list[str], str]:
    docs = await _get_docs(question, top_k)
    docs = docs[:top_k]
    sources = list({d.metadata.get("source", "unknown") for d in docs})
    context = format_docs(docs)
    return docs, sources, context


def _ndjson_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _make_title(question: str) -> str:
    compact = " ".join(question.split())
    return compact[:80] or "New chat"


async def _get_or_create_session(db: AsyncSession, user: User, requested_session_id: int | None, question: str) -> ChatSession:
    if requested_session_id is not None:
        result = await db.execute(
            select(ChatSession)
            .filter(ChatSession.id == requested_session_id, ChatSession.user_id == user.id)
        )
        existing_session = result.scalar_one_or_none()
        if existing_session is not None:
            return existing_session

        logger.info(
            "Requested chat session %s for user %s was not found; creating a new session.",
            requested_session_id,
            user.id,
        )

    chat_session = ChatSession(
        user_id=user.id,
        title=_make_title(question),
        updated_at=datetime.utcnow(),
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


async def _create_chat_session(db: AsyncSession, user: User, title: str) -> ChatSession:
    chat_session = ChatSession(
        user_id=user.id,
        title=title,
        updated_at=datetime.utcnow(),
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


async def _save_message(db: AsyncSession, user_id: int, session_id: int, role: str, content: str, sources: list[str] | None = None) -> None:
    message = ChatMessage(
        session_id=session_id,
        user_id=user_id,
        role=role,
        content=content,
        sources=sources or [],
    )
    db.add(message)
    result = await db.execute(select(ChatSession).filter(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is not None:
        session.updated_at = datetime.utcnow()
    await db.commit()


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


async def _consume_anonymous_query(request: Request, db: AsyncSession) -> int:
    client_ip = _get_client_ip(request)
    result = await db.execute(
        select(AnonymousQueryUsage).filter(AnonymousQueryUsage.ip_address == client_ip)
    )
    usage = result.scalar_one_or_none()
    if usage is None:
        usage = AnonymousQueryUsage(
            ip_address=client_ip,
            query_count=1,
            updated_at=datetime.utcnow(),
        )
        db.add(usage)
        await db.commit()
        return ANONYMOUS_QUERY_LIMIT - usage.query_count

    if usage.query_count >= ANONYMOUS_QUERY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Free query limit reached for this IP. Please log in to continue chatting.",
        )

    usage.query_count += 1
    usage.updated_at = datetime.utcnow()
    await db.commit()
    return ANONYMOUS_QUERY_LIMIT - usage.query_count


async def _authorize_query(
    request: Request,
    db: AsyncSession,
    current_user: User | None,
) -> int | None:
    if current_user is not None:
        return None
    return await _consume_anonymous_query(request, db)


@router.get("/sessions", response_model=list[ChatSessionResponse])
async def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = result.scalars().all()
    return [_serialize_session(session) for session in sessions]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
async def get_chat_messages(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chat_session = await get_user_chat_session(db, current_user.id, session_id)
    result = await db.execute(
        select(ChatMessage)
        .filter(ChatMessage.session_id == chat_session.id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return [_serialize_message(message) for message in messages]


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_chat_session_route(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _create_chat_session(db, current_user, "New chat")
    return _serialize_session(session)


@router.delete("/sessions/{session_id}", response_model=StatusResponse)
async def delete_chat_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chat_session = await get_user_chat_session(db, current_user.id, session_id)
    await db.delete(chat_session)
    await db.commit()
    return StatusResponse(status="deleted")


@router.delete("/sessions", response_model=StatusResponse)
async def delete_all_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import delete
    await db.execute(
        delete(ChatMessage).where(ChatMessage.user_id == current_user.id)
    )
    await db.execute(
        delete(ChatSession).where(ChatSession.user_id == current_user.id)
    )
    await db.commit()
    return StatusResponse(status="deleted")


@router.post("", response_model=QueryResponse)
async def ask_question(
    req: QueryRequest,
    request: Request,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_indexed_documents(db)
    anonymous_remaining = await _authorize_query(request, db, current_user)
    session = None
    if current_user is not None:
        session = await _get_or_create_session(db, current_user, req.session_id, req.question)
        await _save_message(db, current_user.id, session.id, "user", req.question)

    try:
        _, sources, context = await _get_sources_and_context(req.question, req.top_k or 5)
        if not context.strip():
            raise HTTPException(status_code=404, detail="No relevant PDF pages found for this question.")
    except (EmbeddingInitializationError, ResponseError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Embeddings are unavailable, so retrieval cannot run. "
                "Pull the configured embedding model in Ollama, reduce embedding batch size if needed, "
                "and verify OLLAMA_URL."
            ),
        ) from exc

    try:
        chain = build_answer_chain(get_llm())
        answer = await chain.ainvoke({"context": context, "question": req.question})
    except Exception as exc:
        logger.warning("Ollama query failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Ollama query failed: {exc}") from exc

    if current_user is not None and session is not None:
        await _save_message(db, current_user.id, session.id, "assistant", answer, sources)
    return QueryResponse(
        answer=answer,
        sources=sources,
        provider="ollama",
        session_id=session.id if session is not None else None,
        anonymous_remaining=anonymous_remaining,
    )


@router.post("/stream")
async def stream_answer(
    req: QueryRequest,
    request: Request,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_indexed_documents(db)
    anonymous_remaining = await _authorize_query(request, db, current_user)
    session = None
    if current_user is not None:
        session = await _get_or_create_session(db, current_user, req.session_id, req.question)
        await _save_message(db, current_user.id, session.id, "user", req.question)

    try:
        _, sources, context = await _get_sources_and_context(req.question, req.top_k or 5)
        if not context.strip():
            raise HTTPException(status_code=404, detail="No relevant PDF pages found for this question.")
    except (EmbeddingInitializationError, ResponseError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Embeddings are unavailable, so retrieval cannot run. "
                "Pull the configured embedding model in Ollama, reduce embedding batch size if needed, "
                "and verify OLLAMA_URL."
            ),
        ) from exc

    async def event_stream():
        answer_parts: list[str] = []
        yield _ndjson_line(
            {
                "type": "meta",
                "sources": sources,
                "provider": "ollama",
                "session_id": session.id if session is not None else None,
                "anonymous_remaining": anonymous_remaining,
            }
        )
        try:
            chain = build_answer_chain(get_llm())
            payload = {"context": context, "question": req.question}
            answer_started = False

            async for chunk in chain.astream(payload):
                if not answer_started:
                    yield _ndjson_line({"type": "start", "provider": "ollama"})
                    answer_started = True
                answer_parts.append(chunk)
                yield _ndjson_line({"type": "token", "content": chunk})

            if not answer_started:
                yield _ndjson_line({"type": "start", "provider": "ollama"})

            answer = "".join(answer_parts).strip()
            if current_user is not None and session is not None:
                async with AsyncSessionLocal() as history_db:
                    await _save_message(history_db, current_user.id, session.id, "assistant", answer, sources)

            yield _ndjson_line(
                {
                    "type": "done",
                    "provider": "ollama",
                    "sources": sources,
                    "session_id": session.id if session is not None else None,
                    "anonymous_remaining": anonymous_remaining,
                }
            )
        except Exception as exc:
            logger.warning("Streaming Ollama query failed: %s", exc)
            yield _ndjson_line({"type": "error", "detail": f"Ollama query failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
