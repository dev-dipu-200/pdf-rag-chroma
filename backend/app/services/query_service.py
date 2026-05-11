# query_service.py
import json
import logging
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, Request, status
import httpx
from ollama import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import EmbeddingInitializationError, ENABLE_OCR, OCR_LANGS, get_llm
from app.models import AnonymousQueryUsage, ChatMessage, ChatSession, PdfDocument, User
from app.services.pdf import extract_and_chunk_pdf, sanitize_text_for_embedding
from app.services.rag import PROMPT_TEMPLATE, build_answer_chain, format_docs

logger = logging.getLogger(__name__)
ANONYMOUS_QUERY_LIMIT = 3
LLM_RETRY_ATTEMPTS = 2
MIN_TERM_OVERLAP = 1


@dataclass
class QueryExecutionResult:
    answer: str
    sources: list[str]
    session_id: int | None
    anonymous_remaining: int | None


@dataclass
class QueryPreparationResult:
    sources: list[str]
    context: str
    session_id: int | None
    anonymous_remaining: int | None
    user_id: int | None


def ndjson_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _embedding_unavailable_error(exc: Exception) -> HTTPException:
    logger.warning("Embedding retrieval failed: %s: %s", type(exc).__name__, exc)
    return HTTPException(
        status_code=503,
        detail=(
            "Embeddings are unavailable, so retrieval cannot run. "
            "Verify the embedding model is available at the configured Ollama endpoint, "
            "reduce EMBEDDING_BATCH_SIZE if indexing is unstable, "
            "and confirm OLLAMA_URL is reachable."
        ),
    )


def _normalize_llm_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()
    if isinstance(exc, (httpx.HTTPError, ResponseError)):
        return "The AI model is temporarily unavailable. Please try again in a moment."
    if "502 bad gateway" in lowered or "status code: 502" in lowered:
        return "The AI model gateway is temporarily unavailable. Please try again in a moment."
    if "runner process has terminated" in lowered or "status code: 500" in lowered:
        return "The AI model stopped while generating the answer. Please try again in a moment."
    return "The AI model is currently busy. Please try again in a moment."


def make_chat_title(question: str) -> str:
    compact = " ".join(question.split())
    return compact[:80] or "New chat"


async def ensure_indexed_documents(db: AsyncSession) -> None:
    result = await db.execute(
        select(PdfDocument.id).filter(PdfDocument.status == "indexed").limit(1)
    )
    has_docs = result.scalar_one_or_none()
    if has_docs is None:
        raise HTTPException(
            status_code=400,
            detail="No indexed PDFs found. Upload PDFs and wait for indexing to finish.",
        )


async def get_or_create_session(
    db: AsyncSession,
    user: User,
    requested_session_id: int | None,
    question: str,
) -> ChatSession:
    if requested_session_id is not None:
        result = await db.execute(
            select(ChatSession).filter(
                ChatSession.id == requested_session_id, ChatSession.user_id == user.id
            )
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
        title=make_chat_title(question),
        updated_at=datetime.utcnow(),
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


async def create_chat_session(db: AsyncSession, user: User, title: str) -> ChatSession:
    chat_session = ChatSession(
        user_id=user.id,
        title=title,
        updated_at=datetime.utcnow(),
    )
    db.add(chat_session)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


async def save_message(
    db: AsyncSession,
    user_id: int,
    session_id: int,
    role: str,
    content: str,
    sources: list[str] | None = None,
) -> None:
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


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


async def consume_anonymous_query(request: Request, db: AsyncSession) -> int:
    client_ip = get_client_ip(request)
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


async def authorize_query(
    request: Request,
    db: AsyncSession,
    current_user: User | None,
) -> int | None:
    if current_user is not None:
        return None
    return await consume_anonymous_query(request, db)


def _question_terms(question: str) -> set[str]:
    return {
        token
        for token in re.findall(r"\w+", question.lower(), flags=re.UNICODE)
        if len(token) > 1
    }


def _document_match_score(question_terms: set[str], content: str, metadata: dict) -> tuple[int, int]:
    normalized = sanitize_text_for_embedding(content).lower()
    overlap = sum(1 for term in question_terms if term in normalized)
    page = int(str(metadata.get("page", "9999")).split("-", 1)[0]) if metadata.get("page") else 9999
    return overlap, -page


async def get_relevant_context(
    question: str,
    top_k: int,
    db: AsyncSession,
    current_user: User | None,
) -> tuple[list, list[str], str]:
    stmt = select(PdfDocument).filter(PdfDocument.status == "indexed")
    if current_user is not None:
        stmt = stmt.filter(PdfDocument.user_id == current_user.id)
    stmt = stmt.order_by(PdfDocument.created_at.asc())
    result = await db.execute(stmt)
    pdf_documents = result.scalars().all()

    docs = []
    for pdf_document in pdf_documents:
        try:
            docs.extend(
                extract_and_chunk_pdf(
                    pdf_document.stored_path,
                    metadata={
                        "source": pdf_document.original_filename,
                        "document_id": str(pdf_document.id),
                        "user_id": str(pdf_document.user_id),
                    },
                    enable_ocr=ENABLE_OCR,
                    ocr_languages=OCR_LANGS,
                    include_tree_documents=False,
                )
            )
        except Exception as exc:
            logger.warning(
                "Parsed-PDF fallback failed for document %s: %s",
                pdf_document.id,
                exc,
            )

    filtered_docs = [
        doc
        for doc in docs
        if doc.metadata.get("content_type") in {"page_chunk", "page"}
    ]

    question_terms = _question_terms(question)
    if question_terms:
        filtered_docs.sort(
            key=lambda doc: _document_match_score(question_terms, doc.page_content, doc.metadata),
            reverse=True,
        )
        filtered_docs = [
            doc
            for doc in filtered_docs
            if _document_match_score(question_terms, doc.page_content, doc.metadata)[0] >= MIN_TERM_OVERLAP
        ]

    final_docs = filtered_docs[:top_k] if filtered_docs else docs[:top_k]
    try:
        final_docs.sort(key=lambda x: int(x.metadata.get("page", 0)))
    except (ValueError, TypeError):
        pass

    sources = list(
        {
            f"{doc.metadata.get('source', 'unknown')} (Pg {doc.metadata.get('page', '?')})"
            for doc in final_docs
        }
    )

    context = format_docs(final_docs)
    return final_docs, sources, context


async def prepare_query_with_session(
    question: str,
    top_k: int,
    request: Request,
    db: AsyncSession,
    current_user: User | None,
    session_id: int | None,
) -> QueryPreparationResult:
    await ensure_indexed_documents(db)
    anonymous_remaining = await authorize_query(request, db, current_user)
    session = None

    if current_user is not None:
        session = await get_or_create_session(db, current_user, session_id, question)
        await save_message(db, current_user.id, session.id, "user", question)

    try:
        _, sources, context = await get_relevant_context(
            question=question,
            top_k=top_k,
            db=db,
            current_user=current_user,
        )
    except (EmbeddingInitializationError, ResponseError, httpx.HTTPError) as exc:
        raise _embedding_unavailable_error(exc) from exc

    return QueryPreparationResult(
        sources=sources,
        context=context,
        session_id=session.id if session is not None else None,
        anonymous_remaining=anonymous_remaining,
        user_id=current_user.id if current_user is not None else None,
    )


async def execute_query(
    question: str,
    top_k: int,
    request: Request,
    db: AsyncSession,
    current_user: User | None,
    session_id: int | None,
) -> QueryExecutionResult:
    prepared = await prepare_query_with_session(
        question=question,
        top_k=top_k,
        request=request,
        db=db,
        current_user=current_user,
        session_id=session_id,
    )

    try:
        answer = await generate_answer(prepared.context, question)
    except Exception as exc:
        logger.error("LLM query failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=_normalize_llm_error(exc),
        ) from exc
    if prepared.user_id is not None and prepared.session_id is not None:
        await save_message(
            db,
            prepared.user_id,
            prepared.session_id,
            "assistant",
            answer,
            prepared.sources,
        )

    return QueryExecutionResult(
        answer=answer,
        sources=prepared.sources,
        session_id=prepared.session_id,
        anonymous_remaining=prepared.anonymous_remaining,
    )


async def generate_answer(context: str, question: str) -> str:
    """Answer from relevant parsed PDF context."""
    if not context.strip():
        return "I couldn't find the answer in the uploaded documents."
    last_exc = None
    for attempt in range(LLM_RETRY_ATTEMPTS):
        try:
            llm = get_llm()
            chain = build_answer_chain(llm)
            answer = (await chain.ainvoke({"context": context, "question": question})).strip()
            if not answer:
                return "I couldn't find the answer in the uploaded documents."
            return answer
        except Exception as exc:
            last_exc = exc
            logger.warning("LLM answer attempt %s failed: %s", attempt + 1, exc)
            if attempt + 1 >= LLM_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(1)
    raise last_exc


async def stream_answer_chunks(context: str, question: str):
    if not context.strip():
        yield "I couldn't find the answer in the uploaded documents."
        return
    last_exc = None
    for attempt in range(LLM_RETRY_ATTEMPTS):
        try:
            llm = get_llm()
            if isinstance(llm, dict) and llm.get("provider") == "public":
                async for chunk in _stream_public_answer(llm, context, question):
                    yield chunk
                return

            chain = build_answer_chain(llm)
            async for chunk in chain.astream({"context": context, "question": question}):
                yield chunk
            return
        except Exception as exc:
            last_exc = exc
            logger.warning("LLM stream attempt %s failed: %s", attempt + 1, exc)
            if attempt + 1 >= LLM_RETRY_ATTEMPTS:
                break
            await asyncio.sleep(1)
    raise last_exc


def _public_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _public_chat_url(base_url: str) -> str:
    cleaned = (base_url or "").rstrip("/")
    if not cleaned:
        raise RuntimeError("Missing PUBLIC_API_BASE_URL or PUBLIC_LLM_BASE_URL.")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


async def _generate_public_answer(llm_config: dict, context: str, question: str) -> str:
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    payload = {
        "model": llm_config["model"],
        "temperature": llm_config.get("temperature", 0.15),
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        response = await client.post(
            _public_chat_url(llm_config["base_url"]),
            headers=_public_headers(llm_config.get("api_key", "")),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Public LLM returned no choices.")
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


async def _stream_public_answer(llm_config: dict, context: str, question: str):
    prompt = PROMPT_TEMPLATE.format(context=context, question=question)
    payload = {
        "model": llm_config["model"],
        "temperature": llm_config.get("temperature", 0.15),
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        async with client.stream(
            "POST",
            _public_chat_url(llm_config["base_url"]),
            headers=_public_headers(llm_config.get("api_key", "")),
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                payload = json.loads(data_str)
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
