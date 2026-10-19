import re
from typing import List
from pathlib import Path
import pymupdf


def clean_paragraph(raw_text: str) -> str:
    """Rejoin hyphenated line breaks and replace newlines with a single space."""
    # Rejoin words broken across lines (e.g., exam-\nple -> example)
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", raw_text)
    # Replace all newline characters with a single space
    text = re.sub(r"[\r\n]+", " ", text)
    # Collapse multiple spaces or tabs into a single space
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_paragraphs(
    pdf_path: str | Path,
    sort_reading_order: bool = True
) -> List[str]:
    """
    Extract clean paragraphs from a PDF file preserving natural reading order,
    ensuring the file handle is properly closed.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found at: {path}")

    paragraphs: List[str] = []

    with pymupdf.open(path) as doc:
        for page in doc:
            # Retrieve blocks sorted in natural reading order (handles multi-column layouts)
            blocks = page.get_text("blocks", sort=sort_reading_order)

            for block in blocks:
                # (x0, y0, x1, y1, text, block_no, block_type)
                # Text blocks have type 0; image blocks have type 1
                if len(block) >= 7 and block[6] != 0:
                    continue

                text = block[4]
                cleaned = clean_paragraph(text)

                if cleaned:
                    paragraphs.append(cleaned)

    return paragraphs


if __name__ == "__main__":
    pdf_file = Path("pdfs/Penguins_ACL.pdf")
    if pdf_file.exists():
        extracted = extract_paragraphs(pdf_file)
        print(f"Total paragraphs:\n{len(extracted)}\n")
        print(f"first paragraph:\n{extracted[0]}\n")
    else:
        print("Sample file not found.")
        
