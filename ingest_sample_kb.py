from pathlib import Path
from app.core.config import get_settings
from app.rag.knowledge import get_document_indexer


def ingest_sample_kb(files: list[Path] | None = None) -> list:
    """Ingest sample knowledge base files using unified DocumentIndexer batch pipeline."""
    settings = get_settings()
    if files is None:
        folder = Path(settings.sample_kb_dir)
        files = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []

    indexer = get_document_indexer()
    results = indexer.index_batch(files)

    total_chunks = sum(r.chunk_count for r in results)
    total_ids = sum(len(r.ids_created) for r in results)
    print(f"\nIndexed {len(files)} files -> {total_chunks} chunks -> {total_ids} vectors")
    return results


if __name__ == "__main__":
    settings = get_settings()
    folder = Path(settings.sample_kb_dir)
    files = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []

    indexer = get_document_indexer()
    results = indexer.index_batch(files)

    total_chunks = sum(r.chunk_count for r in results)
    total_ids = sum(len(r.ids_created) for r in results)
    print(f"\nIndexed {len(files)} files -> {total_chunks} chunks -> {total_ids} vectors")
