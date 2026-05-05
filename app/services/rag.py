# Retrieval + generation logic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

PROMPT_TEMPLATE = """\
You are a helpful assistant for PDF question answering.
Answer the question using ONLY the context below.
If the answer is not present in the context, say that clearly.
Reply in the same language as the user's question.
The question and context may be in Hindi, English, or a mix of both. Use whichever context is relevant.

Context:
{context}

Question: {question}

Answer concisely and accurately:"""


def format_docs(docs):
    return "\n\n".join(
        (
            f"{doc.page_content} "
            f"(source: {doc.metadata.get('source', 'unknown')}, "
            f"page: {doc.metadata.get('page', 'unknown')}, "
            f"type: {doc.metadata.get('content_type', 'unknown')}, "
            f"tree_level: {doc.metadata.get('tree_level', '0')})"
        )
        for doc in docs
    )


def build_rag_chain(llm, retriever):
    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain


def build_answer_chain(llm):
    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    chain = prompt | llm | StrOutputParser()
    return chain
