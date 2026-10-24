from pathlib import Path
from tqdm import tqdm

from app.core.config import get_settings
from app.services.ingestion import load_file, chunk_documents
from app.rag.vectorstore import add_documents

settings = get_settings()

folder = Path(settings.sample_kb_dir)
files = [p for p in folder.iterdir() if p.is_file()]

all_docs = []

all_docs = []
for path in tqdm(files, desc="Loading files", unit="file"):
    all_docs.extend(load_file(path))
    
chunks = chunk_documents(all_docs)

BATCH_SIZE = 10
ids = []
num_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
for i in tqdm(range(0, len(chunks), BATCH_SIZE),
              total=num_batches,
              desc="Indexing chunks",
              unit="batch"):
    ids.extend(add_documents(chunks[i:i + BATCH_SIZE]))
    
print(f"\nIndexed {len(files)} files -> {len(chunks)} chunks -> {len(ids)} Pinecone vectors")