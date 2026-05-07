from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user, get_user_chat_session
from app.database import AsyncSessionLocal, get_db
from app.dependencies import get_llm
from app.models import ChatMessage, ChatSession, User
from app.schemas import (
    ChatMessageResponse,
    ChatSessionResponse,
    QueryRequest,
    QueryResponse,
    StatusResponse,
)
from app.services.query_service import (
    create_chat_session,
    execute_query,
    generate_answer,
    logger,
    ndjson_line,
    prepare_query_with_session,
    save_message,
    stream_answer_chunks,
)

router = APIRouter(prefix="/query", tags=["query"])


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
    session = await create_chat_session(db, current_user, "New chat")
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

    await db.execute(delete(ChatMessage).where(ChatMessage.user_id == current_user.id))
    await db.execute(delete(ChatSession).where(ChatSession.user_id == current_user.id))
    await db.commit()
    return StatusResponse(status="deleted")


@router.post("", response_model=QueryResponse)
async def ask_question(
    req: QueryRequest,
    request: Request,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    result = await execute_query(
        question=req.question,
        top_k=req.top_k or 5,
        request=request,
        db=db,
        current_user=current_user,
        session_id=req.session_id,
    )
    return QueryResponse(
        answer=result.answer,
        sources=result.sources,
        provider="configured",
        session_id=result.session_id,
        anonymous_remaining=result.anonymous_remaining,
    )


@router.post("/stream")
async def stream_answer(
    req: QueryRequest,
    request: Request,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    prepared = await prepare_query_with_session(
        question=req.question,
        top_k=req.top_k or 5,
        request=request,
        db=db,
        current_user=current_user,
        session_id=req.session_id,
    )

    async def event_stream():
        answer_parts: list[str] = []
        yield ndjson_line(
            {
                "type": "meta",
                "sources": prepared.sources,
                "provider": "configured",
                "session_id": prepared.session_id,
                "anonymous_remaining": prepared.anonymous_remaining,
            }
        )
        try:
            answer_started = False

            async for chunk in stream_answer_chunks(prepared.context, req.question):
                if not answer_started:
                    yield ndjson_line({"type": "start", "provider": "configured"})
                    answer_started = True
                answer_parts.append(chunk)
                yield ndjson_line({"type": "token", "content": chunk})

            if not answer_started:
                yield ndjson_line({"type": "start", "provider": "configured"})

            answer = "".join(answer_parts).strip()
            if prepared.user_id is not None and prepared.session_id is not None:
                async with AsyncSessionLocal() as history_db:
                    await save_message(
                        history_db,
                        prepared.user_id,
                        prepared.session_id,
                        "assistant",
                        answer,
                        prepared.sources,
                    )

            yield ndjson_line(
                {
                    "type": "done",
                    "provider": "configured",
                    "sources": prepared.sources,
                    "session_id": prepared.session_id,
                    "anonymous_remaining": prepared.anonymous_remaining,
                }
            )
        except Exception as exc:
            logger.warning("Streaming LLM query failed: %s", exc)
            yield ndjson_line({"type": "error", "detail": f"LLM query failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
