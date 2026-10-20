import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import SecretStr

load_dotenv()

# --- Project Paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF_PATH = Path(os.getenv("DEFAULT_PDF_PATH", str(PROJECT_ROOT / "pdfs" / "Penguins_ACL.pdf")))
INDEX_PATH = Path(os.getenv("INDEX_PATH", str(PROJECT_ROOT / "data" / "vector_store.json")))

# --- API Settings ---
BASE_URL = os.getenv("BASE_URL", "https://api.gapgpt.app/v1")
api_key = os.getenv("API_KEY")
if not api_key:
    raise RuntimeError(
        "API_KEY is not set. Add it to your .env file, e.g. API_KEY=sk-..."
    )
API_KEY = SecretStr(api_key)

# --- Models ---
MODEL = os.getenv("MODEL", "deepseek-v4-flash")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-large") 
# CONTEXT_WINDOW = int(os.getenv("CONTEXT_WINDOW", "128000"))

if __name__ == "__main__":
    print(BASE_URL, MODEL, EMBED_MODEL, INDEX_PATH)