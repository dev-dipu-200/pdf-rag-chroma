# PDF extract + chunk logic
import fitz  # PyMuPDF
import re
from uuid import uuid4
from langchain_core.documents import Document


def _normalize_page_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_and_chunk_pdf(filepath: str, text_splitter) -> list[Document]:
    doc = fitz.open(filepath)
    documents = []
    chunk_index = 0

    try:
        for page_number, page in enumerate(doc, start=1):
            page_text = _normalize_page_text(page.get_text("text"))
            if not page_text:
                continue

            chunks = text_splitter.split_text(page_text)
            for chunk in chunks:
                documents.append(Document(
                    page_content=chunk,
                    metadata={
                        "source": filepath.split("/")[-1],
                        "chunk_id": str(uuid4()),
                        "chunk_index": chunk_index,
                        "page": page_number,
                    }
                ))
                chunk_index += 1
    finally:
        doc.close()

    if not documents:
        raise ValueError(
            "No readable text was extracted from the PDF. "
            "If this is a scanned PDF, OCR is required before indexing."
        )

    return documents
