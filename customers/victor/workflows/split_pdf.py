"""Split an unsigned supporting PDF into page-complete files below PJe's limit."""

import io
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

LIMIT = 10_000_000


def split_pdf(source, destination):
    source = Path(source)
    data = source.read_bytes()
    if b"/ByteRange" in data:
        raise ValueError("Signed PDF needs manual handling; splitting invalidates signatures")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF needs manual handling")
    if not reader.pages:
        raise ValueError("PDF has no pages")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    parts = []
    written = []
    try:
        start = 0
        while start < len(reader.pages):
            low, high = start + 1, len(reader.pages)
            selected = None
            while low <= high:
                end = (low + high) // 2
                writer = PdfWriter()
                for page in reader.pages[start:end]:
                    writer.add_page(page)
                buffer = io.BytesIO()
                writer.write(buffer)
                if buffer.tell() <= LIMIT:
                    selected = (end, buffer.getvalue())
                    low = end + 1
                else:
                    high = end - 1
            if selected is None:
                raise ValueError(f"Page {start + 1} exceeds PJe's 10 MB limit by itself")
            end, content = selected
            target = destination / f"{source.stem}-vol-{len(parts) + 1:02}.pdf"
            target.write_bytes(content)
            written.append(target)
            target.chmod(0o600)
            if len(PdfReader(target, strict=True).pages) != end - start:
                raise ValueError(f"PDF split verification failed for {target.name}")
            parts.append({"path": str(target), "startPage": start + 1, "endPage": end})
            start = end
    except Exception:
        for target in written:
            target.unlink(missing_ok=True)
        raise
    return parts


if __name__ == "__main__":
    try:
        print(json.dumps(split_pdf(sys.argv[1], sys.argv[2])))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
