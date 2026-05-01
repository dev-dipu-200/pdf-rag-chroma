# Pydantic models
from pydantic import BaseModel
from typing import List, Optional

class QueryRequest(BaseModel):
    question: str
    top_k: Optional[int] = 5

class QueryResponse(BaseModel):
    answer: str
    sources: List[str] = []
    provider: Optional[str] = None

class IngestResponse(BaseModel):
    filename: str
    chunks_added: int
    status: str


class ReindexResponse(BaseModel):
    files_indexed: int
    chunks_added: int
    status: str
