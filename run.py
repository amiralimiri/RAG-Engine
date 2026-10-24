from app.rag.fix_single_llm import ask as ask_llm
from app.rag.fix_single_jev import ask as ask_jev

result = ask_llm(
    "In Agentic RAG, what happens when retrieved documents are not relevant?",
)
print("#"*50)
print(result)
print("#"*50)


result = ask_jev(
    "In Agentic RAG, what happens when retrieved documents are not relevant?",
)
print("#"*50)
print(result)
print("#"*50)