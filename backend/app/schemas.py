from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

class QueryRequest(BaseModel):
    question: str
    top_k: Optional[int] = 3
    session_id: Optional[int] = None

class QueryResponse(BaseModel):
    answer: str
    sources: List[str] = Field(default_factory=list)
    provider: Optional[str] = None
    session_id: Optional[int] = None
    anonymous_remaining: Optional[int] = None


class ParsedQueryResponse(BaseModel):
    content: str
    sources: List[str] = Field(default_factory=list)
    session_id: Optional[int] = None
    anonymous_remaining: Optional[int] = None

class AuthRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PdfDocumentResponse(BaseModel):
    id: int
    original_filename: str
    status: str
    chunks_added: int
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ChatSessionResponse(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class StatusResponse(BaseModel):
    status: str


class ChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    sources: List[str] = Field(default_factory=list)
    created_at: datetime


class IngestResponse(BaseModel):
    document: PdfDocumentResponse
    status: str


class MultiIngestResponse(BaseModel):
    documents: List[PdfDocumentResponse] = Field(default_factory=list)
    queued_documents: int
    status: str


class ReindexResponse(BaseModel):
    queued_documents: int
    status: str

class PaginatedPdfDocumentsResponse(BaseModel):
    items: List[PdfDocumentResponse]
    total: int
    page: int
    pages: int
    size: int
