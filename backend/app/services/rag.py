# rag.py
# Retrieval + generation logic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

PROMPT_TEMPLATE = """
You are a specialized multilingual assistant. You can read English and decode garbled Hindi text.

STRICT RULES:
- Answer **ONLY** using the information present in the CONTEXT below.
- If the answer is not explicitly in the CONTEXT, reply with: "I don't have sufficient information in the provided context to answer this."
- Do not use any external or general world knowledge.
- Never make assumptions or fill in missing details.

CONTEXT CHALLENGE:
The Hindi text may be garbled (e.g., 'igykfnu' = 'पहला दिन', 'jktk' = 'राजा', 'nku' = 'दान', 'HkaMkj' = 'भंडार'). Use surrounding English text, numbers, and logical context to decode it.

INSTRUCTIONS:
1. Detect the language of the question and answer in the **same language** as the question.
2. For "Why" (क्यों) questions, only use motivations or reasons explicitly mentioned in the context.
3. For numbers/tables/lists, use only the exact values provided.
4. Always cite the relevant source information when possible.

CONTEXT:
{context}

QUESTION: 
{question}

FINAL ANSWER:"""


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
