import base64
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from jurisync.connectors.inss.auth import is_reusable_auth, safe_url
from jurisync.connectors.inss.client import (
    attachment_filename,
    process_report_request,
    read_saved_document,
    save_base64_download,
    slice_text,
    task_search_request,
)
from jurisync.core.pdf import attachment_mime_type, extract_pdf_metadata


def tiny_pdf(text):
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    pdf, offsets = "%PDF-1.4\n", []
    for index, item in enumerate(objects):
        offsets.append(len(pdf.encode()))
        pdf += f"{index + 1} 0 obj\n{item}\nendobj\n"
    xref = len(pdf.encode())
    entries = "\n".join(f"{offset:010d} 00000 n " for offset in offsets)
    pdf += f"xref\n0 6\n0000000000 65535 f \n{entries}\ntrailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return pdf.encode()


def jwt(claims):
    return ".".join(["header", base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("="), "signature"])


class InssTest(unittest.TestCase):
    def test_task_search_maps_pages_and_captured_filters(self):
        request = task_search_request(
            page=3, pageSize=20, protocolo="123", token="captcha", now=datetime(2026, 9, 22, 12, tzinfo=UTC)
        )
        self.assertTrue(request["path"].endswith("?first=40&pageSize=20"))
        self.assertEqual(
            request["body"],
            {
                "cpf": "",
                "nome": "",
                "protocolo": "123",
                "status": "",
                "tokenReCAPTCHA": "captcha",
                "dataAtualizacaoInicial": "2026-03-26T03:00:00.000Z",
                "dataAtualizacaoFinal": "2026-09-22T03:00:00.000Z",
                "situacaoTarefa": "",
            },
        )

    def test_task_search_normalizes_calendar_dates(self):
        body = task_search_request(dataAtualizacaoInicial="2026-09-01", dataAtualizacaoFinal="2026-09-10")["body"]
        self.assertEqual(
            (body["dataAtualizacaoInicial"], body["dataAtualizacaoFinal"]),
            ("2026-09-01T03:00:00.000Z", "2026-09-10T03:00:00.000Z"),
        )
        with self.assertRaisesRegex(ValueError, "Invalid INSS search date"):
            task_search_request(dataAtualizacaoInicial="yesterday")

    def test_attachment_filenames_cannot_escape_download_directory(self):
        self.assertEqual(attachment_filename('attachment; filename="documento.pdf"', "fallback.bin"), "documento.pdf")
        self.assertEqual(attachment_filename('attachment; filename="../../secret.pdf"', "fallback.bin"), "secret.pdf")
        self.assertEqual(attachment_filename("renamed.pdf", "fallback.bin"), "renamed.pdf")
        self.assertEqual(attachment_mime_type(b"%PDF-1.7", "fallback.bin"), "application/pdf")

    def test_consolidated_report_derives_cpf_and_name_from_gerid(self):
        auth = jwt({"cpf": "02806598222", "nome": "João da Silva"})
        self.assertEqual(
            process_report_request("120143833", auth),
            "/apis/consolidadorECApi/pap/metadados?protocolo=120143833&sistema=PAT&cpf=02806598222&nome=Jo%C3%A3o+da+Silva&async=false",
        )
        with self.assertRaisesRegex(ValueError, "CPF and name"):
            process_report_request("1", jwt({"cpf": "1"}))
        with self.assertRaisesRegex(ValueError, "only digits"):
            process_report_request("1; drop", auth)

    def test_pdf_downloads_return_markdown_and_never_overwrite(self):
        self.assertIn("Hello INSS", extract_pdf_metadata(tiny_pdf("Hello INSS"))["markdown"])
        encoded = base64.b64encode(tiny_pdf("Hello INSS")).decode()
        with tempfile.TemporaryDirectory() as directory:
            first = save_base64_download(encoded, None, "1-processo.pdf", "application/pdf", directory)
            second = save_base64_download(encoded, None, "1-processo.pdf", "application/pdf", directory)
            self.assertEqual(
                (Path(first["path"]).name, Path(second["path"]).name), ("1-processo.pdf", "1-processo-1.pdf")
            )
            self.assertEqual((first["pageCount"], first["mimeType"]), (1, "application/pdf"))
            self.assertIn("Hello INSS", first["markdown"])
            ocr_pages = first.get("pagesNeedingOcr", first.get("pagesRecommendedForOcr"))
            self.assertIsInstance(ocr_pages, list)
            with self.assertRaisesRegex(ValueError, "was not application/pdf"):
                save_base64_download(
                    base64.b64encode(b"not a pdf").decode(), None, "x.pdf", "application/pdf", directory
                )

    def test_headless_reuse_requires_unexpired_credential(self):
        now = 2_000_000_000_000
        self.assertFalse(is_reusable_auth(None, now))
        self.assertTrue(is_reusable_auth(jwt({"exp": now / 1000 + 60}), now))
        self.assertFalse(is_reusable_auth(jwt({"exp": now / 1000 - 60}), now))
        self.assertTrue(is_reusable_auth("opaque-token", now))

    def test_safe_url_drops_tokens(self):
        self.assertEqual(
            safe_url("https://atendimento.inss.gov.br/x?ticket=t&a=1#frag"), "https://atendimento.inss.gov.br/x?a=1"
        )


class ReadDocumentTests(unittest.TestCase):
    def test_text_comes_in_windows_with_a_pointer_to_the_next_one(self):
        parsed = {"markdown": "0123456789", "pageCount": 1}
        first = slice_text(parsed, 0, 4)
        self.assertEqual((first["markdown"], first["nextOffset"], first["textTruncated"]), ("0123", 4, True))
        last = slice_text(parsed, 8, 4)
        self.assertEqual((last["markdown"], last["nextOffset"], last["textTruncated"]), ("89", None, False))
        self.assertEqual(last["pageCount"], 1)

    def test_a_saved_pdf_is_parsed_and_paged_from_the_download_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "processo.pdf").write_bytes(tiny_pdf("Sentenca procedente"))
            whole = read_saved_document("processo.pdf", 0, 100_000, folder)
            self.assertIn("Sentenca procedente", whole["markdown"])
            self.assertEqual((whole["pageCount"], whole["nextOffset"]), (1, None))
            part = read_saved_document("processo.pdf", 0, 5, folder)
            self.assertEqual(part["markdown"], whole["markdown"][:5])
            self.assertEqual(part["nextOffset"], 5)

    def test_only_a_bare_file_name_inside_the_download_folder_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder).parent / "secret.pdf"
            outside.write_bytes(tiny_pdf("secret"))
            try:
                for name in ("../secret.pdf", "sub/x.pdf", "/etc/passwd", "..", "", "a\\b.pdf"):
                    with self.assertRaises(ValueError, msg=name):
                        read_saved_document(name, 0, 100, folder)
                with self.assertRaises(FileNotFoundError):
                    read_saved_document("missing.pdf", 0, 100, folder)
            finally:
                outside.unlink()

    def test_get_tools_cap_the_returned_text_and_point_to_the_rest(self):
        from jurisync.connectors.inss.server import file_result

        result = file_result({"filename": "p.pdf", "markdown": "x" * 5000, "base64": "AAAA"}, 2000)
        structured = result.structured_content
        self.assertEqual((structured["textReturnedCharacters"], structured["nextOffset"]), (2000, 2000))
        self.assertTrue(structured["textTruncated"])
        self.assertNotIn("base64", structured)
        self.assertEqual(len(result.content[1].text.split("\n\n", 1)[1]), 2000)

    def test_tools_use_the_get_names_and_a_read_tool_is_registered(self):
        import asyncio

        from mcp.client.client import Client

        from jurisync.connectors.inss import server

        async def names():
            async with Client(server.server) as client:
                return {tool.name for tool in (await client.list_tools()).tools}

        tools = asyncio.run(names())
        self.assertTrue({"inss_get_process", "inss_get_attachment", "inss_read_document"} <= tools)
        self.assertFalse({"inss_download_process", "inss_download_attachment"} & tools)


if __name__ == "__main__":
    unittest.main()
