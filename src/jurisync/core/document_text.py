"""Small, portal-neutral document body extractor."""

from io import BytesIO

from bs4 import BeautifulSoup

from jurisync.core.transcription import transcribe_bytes

MAX_EXTRACTED_CHARACTERS = 1_000_000


def _html_status(body):
    lowered = body.decode("iso-8859-1", "ignore").casefold()
    if any(
        token in lowered
        for token in (
            "acesso negado",
            "access denied",
            "sem acesso ao documento",
            "código de segurança inválido",
            "codigo de seguranca invalido",
            "não autorizado",
            "nao autorizado",
            "documento sigiloso",
        )
    ):
        return "access_denied"
    try:
        soup = BeautifulSoup(body, "html.parser")
        title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
        if soup.select_one("input[type=password], form[action*='login']") or any(
            token in title for token in ("login", "entrar", "sessão expirada", "sessao expirada")
        ):
            return "session_expired"
    except Exception:
        pass
    return None


UNNUMBERED_MIN_CHARACTERS = 80
NOT_A_DOCUMENT_MARKERS = (
    "não é válido ou não existe",
    "nao e valido ou nao existe",
    "indisponível",
    "indisponivel",
    "pjeoffice",
    "sessão expirada",
    "sessao expirada",
    "acesso negado",
    "protegido por sigilo",
)


def _looks_like_document(text):
    lowered = text.casefold()
    return len(text) >= UNNUMBERED_MIN_CHARACTERS and not any(marker in lowered for marker in NOT_A_DOCUMENT_MARKERS)


def _pdf_has_images(reader):
    for page in reader.pages:
        try:
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject") or {}
            for value in xobjects.values():
                if value.get_object().get("/Subtype") == "/Image":
                    return True
        except Exception:
            continue
    return False


def extract_document_text(content, content_type="", process_number="", *, allow_unnumbered=False):
    """Return ``(status, text)`` without treating portal/error pages as evidence.

    HTML that does not print the process number is normally rejected as an unknown page. With
    ``allow_unnumbered`` (for callers that already fetched the document by a process-bound ID), a
    substantial page without error or login markers is returned as ``extracted_unnumbered``.
    """
    if not content or not content.strip():
        return "empty", ""
    content_type = content_type or ""
    if content_type.casefold().startswith("audio/") or content.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3")):
        return transcribe_bytes(content)
    looks_html = content.lstrip().lower().startswith((b"<html", b"<!doctype html"))
    is_pdf = content.lstrip().startswith(b"%PDF-") or ("pdf" in content_type.casefold() and not looks_html)
    if is_pdf:
        try:
            from pypdf import PdfReader
        except ImportError:
            return "pdf_extractor_unavailable", ""
        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                try:
                    if not reader.decrypt(""):
                        return "encrypted_pdf", ""
                except Exception:
                    return "encrypted_pdf", ""
            pages = []
            size = 0
            for page in reader.pages:
                value = page.extract_text() or ""
                pages.append(value)
                size += len(value)
                if size >= MAX_EXTRACTED_CHARACTERS:
                    break
            text = " ".join(" ".join(pages).split())[:MAX_EXTRACTED_CHARACTERS]
            if text:
                return "extracted", text
            return ("ocr_needed", "") if _pdf_has_images(reader) else ("empty", "")
        except Exception:
            return "pdf_parse_error", ""
    if "html" not in content_type.casefold() and not looks_html:
        return "unsupported_content", ""
    status = _html_status(content)
    if status:
        return status, ""
    soup = BeautifulSoup(content, "html.parser")
    for node in soup.select("script, style, head, iframe, object, embed, [hidden], [aria-hidden='true']"):
        node.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())[:MAX_EXTRACTED_CHARACTERS]
    if not text:
        return "empty", ""
    if process_number:
        digits = "".join(ch for ch in process_number if ch.isdigit())
        haystack = "".join(ch for ch in text if ch.isdigit())
        if digits not in haystack:
            if allow_unnumbered and _looks_like_document(text):
                return "extracted_unnumbered", text
            return "unrecognized_html", ""
    return "extracted", text
