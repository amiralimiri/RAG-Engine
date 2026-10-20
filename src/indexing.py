import argparse
from pathlib import Path
from typing import Optional

from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

from src import config
from src.file_reader import extract_paragraphs


def get_embedding_model() -> OpenAIEmbeddings:
    """Instantiate and return the OpenAI embeddings model."""
    return OpenAIEmbeddings(
        model=config.EMBED_MODEL,
        base_url=config.BASE_URL,
        api_key=config.API_KEY,
    )


# In-memory singleton cache to avoid repeated disk reads or re-indexing within a single process
_cached_vector_store: Optional[InMemoryVectorStore] = None


def build_vector_store(
    pdf_path: str | Path = config.DEFAULT_PDF_PATH,
    save_path: Optional[str | Path] = config.INDEX_PATH,
    embedding: Optional[OpenAIEmbeddings] = None,
) -> InMemoryVectorStore:
    """Extract paragraphs from PDF, generate embeddings, and optionally persist the vector store to disk."""
    if embedding is None:
        embedding = get_embedding_model()

    pdf_path = Path(pdf_path)
    chunks = extract_paragraphs(pdf_path)

    store = InMemoryVectorStore(embedding)
    store.add_texts(texts=chunks)
    print(f"Indexed {len(chunks)} chunks from {pdf_path}")

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        store.dump(str(save_path))
        print(f"Saved vector store to {save_path}")

    return store


def load_vector_store(
    save_path: str | Path = config.INDEX_PATH,
    embedding: Optional[OpenAIEmbeddings] = None,
) -> InMemoryVectorStore:
    """Load a persistent vector store from disk without regenerating embeddings."""
    if embedding is None:
        embedding = get_embedding_model()

    save_path = Path(save_path)
    if not save_path.is_file():
        raise FileNotFoundError(f"Vector store file not found at: {save_path}")

    store = InMemoryVectorStore.load(str(save_path), embedding=embedding)
    return store


def get_vector_store(
    pdf_path: str | Path = config.DEFAULT_PDF_PATH,
    index_path: str | Path = config.INDEX_PATH,
    force_rebuild: bool = False,
) -> InMemoryVectorStore:
    """
    Get or initialize the vector store instance (in-memory cached singleton).
    - If already loaded in memory and not force_rebuild: returns cached instance immediately.
    - If index_path exists on disk and not force_rebuild: loads cached index from disk without calling embedding API.
    - Otherwise: extracts PDF paragraphs, generates embeddings, saves to disk, and caches in memory.
    """
    global _cached_vector_store

    if _cached_vector_store is not None and not force_rebuild:
        return _cached_vector_store

    index_path = Path(index_path)
    embedding = get_embedding_model()

    if index_path.is_file() and not force_rebuild:
        print(f"Loading existing vector store from {index_path}")
        _cached_vector_store = load_vector_store(save_path=index_path, embedding=embedding)
    else:
        print(f"Index not found or rebuild requested. Building vector store at {index_path}...")
        _cached_vector_store = build_vector_store(
            pdf_path=pdf_path, save_path=index_path, embedding=embedding
        )

    return _cached_vector_store


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build or inspect the vector store index")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild index from source PDF and overwrite persistent index",
    )
    args = parser.parse_args()

    vs = get_vector_store(force_rebuild=args.rebuild)
    print(f"Vector store ready with index at {config.INDEX_PATH}")