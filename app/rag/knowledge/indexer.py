"""Deep document indexing pipeline coordinating parsing, chunking, and deterministic upserts."""

import asyncio
import hashlib
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.knowledge.base import IndexingResult, VectorStoreAdapter
from app.rag.knowledge.chunkers import DocumentChunker, RecursiveCharacterChunker
from app.rag.knowledge.parsers import CompositeDocumentParser, DocumentParser
from app.rag.knowledge.vectorstore import InMemoryVectorStoreAdapter


class DocumentIndexer:
    """Orchestrates document loading, chunking, deterministic chunk ID generation, and vector persistence."""

    def __init__(
        self,
        store: VectorStoreAdapter,
        parser: DocumentParser | None = None,
        chunker: DocumentChunker | None = None,
        batch_size: int = 10,
    ) -> None:
        self.store = store
        self.parser = parser or CompositeDocumentParser()
        self.chunker = chunker or RecursiveCharacterChunker()
        self.batch_size = max(1, batch_size)

    def _generate_chunk_ids(self, path: Path | str, chunks: list) -> list[str]:
        """Generate deterministic SHA-256 chunk IDs based on filename, index, and content."""
        file_name = Path(path).name
        ids: list[str] = []
        for idx, chunk in enumerate(chunks):
            content_hash = hashlib.sha256(chunk.page_content.encode("utf-8")).hexdigest()[:16]
            ids.append(f"{file_name}:{idx}:{content_hash}")
        return ids

    def index_file(self, path: Path | str) -> IndexingResult:
        """Synchronously parse, chunk, and index a document file into the vector store."""
        str_path = str(path)
        try:
            docs = self.parser.parse(path)
            chunks = self.chunker.chunk(docs)
            ids = self._generate_chunk_ids(path, chunks)

            for chunk in chunks:
                chunk.metadata.setdefault("source", str_path)

            if chunks:
                step = max(1, self.batch_size)
                for i in range(0, len(chunks), step):
                    self.store.add_documents(chunks[i : i + step], ids=ids[i : i + step])

            return IndexingResult(
                source=str_path,
                document_count=len(docs),
                chunk_count=len(chunks),
                ids_created=ids,
                success=True,
                error=None,
            )
        except Exception as exc:
            return IndexingResult(
                source=str_path,
                document_count=0,
                chunk_count=0,
                ids_created=[],
                success=False,
                error=str(exc),
            )

    async def aindex_file(self, path: Path | str) -> IndexingResult:
        """Asynchronously parse, chunk, and index a document file into the vector store."""
        str_path = str(path)
        try:
            if hasattr(self.parser, "aparse"):
                docs = await self.parser.aparse(path)
            else:
                docs = await asyncio.to_thread(self.parser.parse, path)

            chunks = self.chunker.chunk(docs)
            ids = self._generate_chunk_ids(path, chunks)

            for chunk in chunks:
                chunk.metadata.setdefault("source", str_path)

            if chunks:
                step = max(1, self.batch_size)
                for i in range(0, len(chunks), step):
                    await self.store.aadd_documents(chunks[i : i + step], ids=ids[i : i + step])

            return IndexingResult(
                source=str_path,
                document_count=len(docs),
                chunk_count=len(chunks),
                ids_created=ids,
                success=True,
                error=None,
            )
        except Exception as exc:
            return IndexingResult(
                source=str_path,
                document_count=0,
                chunk_count=0,
                ids_created=[],
                success=False,
                error=str(exc),
            )

    def index_batch(
        self,
        paths: list[Path | str],
        batch_size: int = 10,
    ) -> list[IndexingResult]:
        """Synchronously index a collection of documents in batched slices."""
        results: list[IndexingResult] = []
        if not paths:
            return results
        step = max(1, batch_size)
        for i in range(0, len(paths), step):
            batch = paths[i : i + step]
            for p in batch:
                results.append(self.index_file(p))
        return results

    async def aindex_batch(
        self,
        paths: list[Path | str],
        batch_size: int = 10,
    ) -> list[IndexingResult]:
        """Asynchronously index a collection of documents concurrently in batched slices."""
        results: list[IndexingResult] = []
        if not paths:
            return results
        step = max(1, batch_size)
        for i in range(0, len(paths), step):
            batch = paths[i : i + step]
            batch_results = await asyncio.gather(*(self.aindex_file(p) for p in batch))
            results.extend(batch_results)
        return results


def get_document_indexer(
    store: VectorStoreAdapter | None = None,
    settings: Settings | None = None,
    parser: DocumentParser | None = None,
    chunker: DocumentChunker | None = None,
    batch_size: int = 10,
) -> DocumentIndexer:
    """Factory creating a DocumentIndexer configured with active settings and dependencies."""
    cfg = settings or get_settings()

    resolved_store: VectorStoreAdapter
    if store is not None:
        resolved_store = store
    else:
        try:
            from app.rag.knowledge.vectorstore import get_knowledge_store

            resolved_store = get_knowledge_store(settings=settings)
        except (ImportError, AttributeError):
            resolved_store = InMemoryVectorStoreAdapter()

    resolved_parser = parser or CompositeDocumentParser()
    resolved_chunker = chunker or RecursiveCharacterChunker(settings=cfg)

    return DocumentIndexer(
        store=resolved_store,
        parser=resolved_parser,
        chunker=resolved_chunker,
        batch_size=batch_size,
    )
