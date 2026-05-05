import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ollama import ResponseError
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_user_chat_session
from app.database import SessionLocal, get_db
from app.dependencies import (
    EmbeddingInitializationError,
    get_llm,
    get_vectorstore,
    shared_collection_name,
)
from app.models import ChatMessage, ChatSession, PdfDocument, User
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


def _ensure_indexed_documents(db: Session) -> None:
    has_docs = (
        db.query(PdfDocument.id)
        .filter(PdfDocument.status == "indexed")
        .first()
    )
    if has_docs is None:
        raise HTTPException(
            status_code=400,
            detail="No indexed PDFs found for this user. Upload PDFs and wait for indexing to finish.",
        )


def _get_docs(question: str, top_k: int):
    vectorstore = get_vectorstore(shared_collection_name())
    return vectorstore.similarity_search(question, k=max(top_k * 2, top_k + 2))


def _get_sources_and_context(question: str, top_k: int) -> tuple[list, list[str], str]:
    docs = _get_docs(question, top_k)[:top_k]
    sources = list({d.metadata.get("source", "unknown") for d in docs})
    context = format_docs(docs)
    return docs, sources, context


def _ndjson_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _make_title(question: str) -> str:
    compact = " ".join(question.split())
    return compact[:80] or "New chat"


def _get_or_create_session(db: Session, user: User, requested_session_id: int | None, question: str) -> ChatSession:
    if requested_session_id is not None:
        existing_session = (
            db.query(ChatSession)
            .filter(ChatSession.id == requested_session_id, ChatSession.user_id == user.id)
            .first()
        )
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
    db.commit()
    db.refresh(chat_session)
    return chat_session


def _create_chat_session(db: Session, user: User, title: str) -> ChatSession:
    chat_session = ChatSession(
        user_id=user.id,
        title=title,
        updated_at=datetime.utcnow(),
    )
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)
    return chat_session


def _save_message(db: Session, user_id: int, session_id: int, role: str, content: str, sources: list[str] | None = None) -> None:
    message = ChatMessage(
        session_id=session_id,
        user_id=user_id,
        role=role,
        content=content,
        sources=sources or [],
    )
    db.add(message)
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is not None:
        session.updated_at = datetime.utcnow()
    db.commit()


@router.get("/sessions", response_model=list[ChatSessionResponse])
def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return [_serialize_session(session) for session in sessions]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
def get_chat_messages(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_session = get_user_chat_session(db, current_user.id, session_id)
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == chat_session.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return [_serialize_message(message) for message in messages]


@router.post("/sessions", response_model=ChatSessionResponse)
def create_chat_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _serialize_session(_create_chat_session(db, current_user, "New chat"))


@router.delete("/sessions/{session_id}", response_model=StatusResponse)
def delete_chat_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chat_session = get_user_chat_session(db, current_user.id, session_id)
    db.delete(chat_session)
    db.commit()
    return StatusResponse(status="deleted")


@router.delete("/sessions", response_model=StatusResponse)
def delete_all_chat_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return StatusResponse(status="deleted")


@router.post("", response_model=QueryResponse)
def ask_question(
    req: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_indexed_documents(db)
    session = _get_or_create_session(db, current_user, req.session_id, req.question)
    _save_message(db, current_user.id, session.id, "user", req.question)

    try:
        _, sources, context = _get_sources_and_context(req.question, req.top_k or 5)
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
        answer = chain.invoke({"context": context, "question": req.question})
    except Exception as exc:
        logger.warning("Ollama query failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Ollama query failed: {exc}") from exc

    _save_message(db, current_user.id, session.id, "assistant", answer, sources)
    return QueryResponse(
        answer=answer,
        sources=sources,
        provider="ollama",
        session_id=session.id,
    )


@router.post("/stream")
def stream_answer(
    req: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_indexed_documents(db)
    session = _get_or_create_session(db, current_user, req.session_id, req.question)
    _save_message(db, current_user.id, session.id, "user", req.question)

    try:
        _, sources, context = _get_sources_and_context(req.question, req.top_k or 5)
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

    def event_stream():
        answer_parts: list[str] = []
        yield _ndjson_line(
            {
                "type": "meta",
                "sources": sources,
                "provider": "ollama",
                "session_id": session.id,
            }
        )
        try:
            chain = build_answer_chain(get_llm())
            payload = {"context": context, "question": req.question}
            answer_started = False

            for chunk in chain.stream(payload):
                if not answer_started:
                    yield _ndjson_line({"type": "start", "provider": "ollama"})
                    answer_started = True
                answer_parts.append(chunk)
                yield _ndjson_line({"type": "token", "content": chunk})

            if not answer_started:
                yield _ndjson_line({"type": "start", "provider": "ollama"})

            answer = "".join(answer_parts).strip()
            history_db = SessionLocal()
            try:
                _save_message(history_db, current_user.id, session.id, "assistant", answer, sources)
            finally:
                history_db.close()

            yield _ndjson_line(
                {
                    "type": "done",
                    "provider": "ollama",
                    "sources": sources,
                    "session_id": session.id,
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
