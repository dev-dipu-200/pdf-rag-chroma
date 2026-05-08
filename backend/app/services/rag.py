# rag.py
# Retrieval + generation logic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

PROMPT_TEMPLATE = """
You are a specialized multilingual assistant capable of reading English and decoding corrupted Hindi text.

CONTEXT CHALLENGE:
The Hindi text in the provided context may appear garbled (e.g., 'igykfnu' instead of 'पहला दिन', 'jktk' instead of 'राजा'). 
The English text is usually clear.

INSTRUCTIONS:
1. **Language Detection**: Identify if the answer is in the English sections, the Hindi sections, or both.
2. **Hindi Decoding**: If you encounter garbled Hindi text, use the surrounding context and numbers to decode the meaning (e.g., 'nku' = 'दान', 'HkaMkj' = 'भंडार'). 
3. **Response Language**: Always answer in the SAME language as the user's question.
4. **Accuracy**: If the user asks "Why" (क्यों), look for motivations or reasons (like the King's greed or fear of an empty treasury).
5. **Tables**: Use the lists and numbers provided to give precise details for mathematical questions.

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
