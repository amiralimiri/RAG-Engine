from src import config
from src.file_reader import extract_paragraphs

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings


embedding = OpenAIEmbeddings(
    model=config.EMBED_MODEL,
    base_url=config.BASE_URL,
    api_key=config.API_KEY,
    # check_embedding_ctx_length=False 
)

vectore_store = InMemoryVectorStore(embedding)

chunks = extract_paragraphs("pdfs/Penguins_ACL.pdf")
vectore_store.add_texts(texts=chunks)
print(f"indexed {len(chunks)} chunks")