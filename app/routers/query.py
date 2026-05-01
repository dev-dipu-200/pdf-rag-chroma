# Ask questions endpoints
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.dependencies import (
    EmbeddingInitializationError,
    get_llm,
    get_vectorstore,
)
from app.schemas import QueryRequest, QueryResponse
from app.services.rag import build_answer_chain, build_rag_chain, format_docs

router = APIRouter(prefix="/query", tags=["query"])
logger = logging.getLogger(__name__)


def _get_retriever(top_k: int):
    vectorstore = get_vectorstore()
    return vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": top_k}
    )


def _get_sources(retriever, question: str) -> tuple[list, list[str], str]:
    docs = retriever.invoke(question)
    sources = list({d.metadata.get("source", "unknown") for d in docs})
    context = format_docs(docs)
    return docs, sources, context


def _ndjson_line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"

@router.post("", response_model=QueryResponse)
def ask_question(
    req: QueryRequest,
):
    try:
        retriever = _get_retriever(req.top_k)
    except EmbeddingInitializationError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Embeddings are unavailable, so retrieval cannot run. "
                "Pull the configured embedding model in Ollama and verify OLLAMA_URL."
            ),
        ) from exc

    try:
        chain = build_rag_chain(get_llm(), retriever)
        answer = chain.invoke(req.question)
    except Exception as exc:
        logger.warning("Ollama query failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Ollama query failed: {exc}",
        ) from exc

    _, sources, _ = _get_sources(retriever, req.question)

    return QueryResponse(answer=answer, sources=sources, provider="ollama")


@router.post("/stream")
def stream_answer(req: QueryRequest):
    try:
        retriever = _get_retriever(req.top_k)
        _, sources, context = _get_sources(retriever, req.question)
    except EmbeddingInitializationError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Embeddings are unavailable, so retrieval cannot run. "
                "Pull the configured embedding model in Ollama and verify OLLAMA_URL."
            ),
        ) from exc

    def event_stream():
        yield _ndjson_line({"type": "meta", "sources": sources})
        try:
            chain = build_answer_chain(get_llm())
            payload = {"context": context, "question": req.question}
            answer_started = False

            for chunk in chain.stream(payload):
                if not answer_started:
                    yield _ndjson_line({"type": "start", "provider": "ollama"})
                    answer_started = True
                yield _ndjson_line({"type": "token", "content": chunk})

            if not answer_started:
                yield _ndjson_line({"type": "start", "provider": "ollama"})
            yield _ndjson_line(
                {"type": "done", "provider": "ollama", "sources": sources}
            )
        except Exception as exc:
            logger.warning("Streaming Ollama query failed: %s", exc)
            yield _ndjson_line(
                {
                    "type": "error",
                    "detail": f"Ollama query failed: {exc}",
                }
            )

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
