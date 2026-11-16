"""Offline tests for ADVBOX folder creation and uploads into customer folders.

The fake below answers like the recorded screen flow (HAR of 2026-11-09, values replaced by placeholders):
``POST /file/search`` returns file-manager ``<li>`` items, ``POST /file/create-folder`` answers ``success``,
``POST /s3`` (multipart, root=customer) answers the stored file's ``<li>``, and ``POST /s3`` with
``_method=DELETE`` removes a file by id.
"""

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl

from curl_cffi.requests.exceptions import Timeout

from jurisync.connectors.advbox import server
from jurisync.connectors.advbox.client import AdvboxClient, UnconfirmedWriteError
from jurisync.connectors.advbox.documents import AdvboxDocuments, folder_name, folder_path
from jurisync.connectors.advbox.parsers import parse_uploaded_file

CUSTOMER = "10000001"


def folder_item(path, name, *, system=False):
    hide = " hide-upload" if system else ""
    return (
        f'<li class="queue sync completed"> <a href="javascript: void(0);" class="item folder open-folder{hide}" '
        f'data-path="{path}"> <i class="thumb material-icons">folder</i> <div class="info"> <p class="name">{name}</p> '
        f'<div class="open-rename-file"><input maxlength="40" data-type="folder" data-item="{path}" '
        f'data-customer="{CUSTOMER}" name="name" type="text" value="{name}"></div> '
        '<p class="datetime">0 itens &bull; 0 B</p> </div> </a> </li>'
    )


def file_item(file_id, stored):
    return (
        f'<li class="queue sync completed"> <a href="files/20000002/{stored}?f={file_id}" class="item open-new-page" '
        'target="_blank"> <img src="images/file-manager-pdf.png" class="thumb"> <div class="info"> '
        f'<p class="name">{stored}</p> <div class="open-rename-file"><input maxlength="140" data-type="file" '
        f'data-item="{file_id}" data-customer="" name="name" type="text" value="{stored}"></div> '
        '<p class="datetime">9 Nov 26 &bull; 12.0 KB</p> </div> </a> <div class="icon-main"> '
        '<a href="javascript: void(0);" class="main-file material-icons">more_vert</a> <ul class="options"> '
        f'<li><a class="open-new-page" href="files/20000002/{stored}?f={file_id}" target="_blank">Abrir</a></li> '
        f'<li class="move"><a class="move-to-folder" href="javascript: void(0);" data-id="{file_id}" '
        'data-move="01-Pasta">01-Pasta</a></li> '
        f'<li><a class="warning delete-file customer-list" href="javascript: void(0);" data-item="{file_id}" '
        'data-file="x">Excluir</a></li> </ul> </div> </li>'
    )


class FakeAdvbox:
    """A customer's file manager as ADVBOX's endpoints expose it."""

    def __init__(self):
        self.folders = {"": ["Anexado em tarefas", "00-PROTOCOLAR", "Documentos"], "00-PROTOCOLAR": []}
        self.files = {}
        self.calls = []
        self.held = 0
        self.next_id = 50000000
        self.multipart_failures = {}  # call number -> exception
        self.delete_failures = set()
        self.create_failure = None
        self.create_answer = "success"
        self.create_stores = True

    # session protocol used by the connector
    def using(self, handler):
        return handler(self)

    @contextlib.contextmanager
    def exclusive(self):
        self.held += 1
        try:
            yield self
        finally:
            self.held -= 1

    def with_csrf(self, build):
        return build("csrf-placeholder")

    def post_form(self, path, body, retry_429=False):
        assert path == "/file/search"
        form = dict(parse_qsl(body, keep_blank_values=True))
        assert (form["root"], form["folder"], form["order"]) == ("customer", CUSTOMER, "name,asc")
        current = form["path_filter"]
        self.calls.append(("search", current, self.held))
        items = [
            folder_item(f"{current}/{name}" if current else name, name, system=name == "Anexado em tarefas")
            for name in self.folders.get(current, [])
        ]
        items += [file_item(file_id, stored) for file_id, stored in self.files.get(current, [])]
        return "\n".join(items)

    def post(self, path, params):
        self.calls.append((path, dict(params), self.held))
        if path == "/file/create-folder":
            assert params["customer"] == CUSTOMER
            parent, _, name = params["path"].rpartition("/")
            if self.create_stores:
                self.folders.setdefault(parent, []).append(name)
                self.folders.setdefault(params["path"], [])
            if self.create_failure:
                raise self.create_failure
            return self.create_answer
        if path == "/s3":
            assert (params["type"], params["customer"], params["_method"]) == ("file", "", "DELETE")
            if params["id"] in self.delete_failures:
                raise RuntimeError("ADVBOX POST /s3 failed (500)")
            for folder, entries in self.files.items():
                self.files[folder] = [entry for entry in entries if entry[0] != params["id"]]
            return ""
        raise AssertionError(path)

    def post_multipart(self, path, fields):
        assert path == "/s3"
        self.calls.append(("upload", fields["file[]"]["filename"], self.held))
        count = sum(1 for call in self.calls if call[0] == "upload")
        assert (fields["root"], fields["folder"]) == ("customer", CUSTOMER)
        failure = self.multipart_failures.get(count)
        if isinstance(failure, Exception):
            raise failure
        if failure == "no-file":
            return "<p>ok</p>"
        self.next_id += 1
        stem, _, suffix = fields["file[]"]["filename"].rpartition(".")
        stored = f"{stem.replace('.', '')}_1762720000.{suffix}"
        self.files.setdefault(fields["path_filter"], []).append((str(self.next_id), stored))
        return file_item(str(self.next_id), stored)

    def kinds(self, kind):
        return [call for call in self.calls if call[0] == kind]


def pdf(name):
    return {"filename": name, "bytes": b"%PDF-1.4 placeholder", "contentType": "application/pdf"}


class FolderCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "true"})
        env.start()
        self.addCleanup(env.stop)
        self.advbox = FakeAdvbox()
        self.documents = AdvboxDocuments(AdvboxClient(session=self.advbox))


class CreateFolder(FolderCase):
    def test_a_root_folder_is_created_under_the_lock_and_confirmed_by_the_listing(self):
        result = self.documents.create_folder(CUSTOMER, "  Nova   pasta ")
        self.assertEqual((result["created"], result["existing"], result["path"]), (True, False, "Nova pasta"))
        self.assertEqual(result["folder"]["path"], "Nova pasta")
        self.assertEqual(
            self.advbox.kinds("/file/create-folder"),
            [("/file/create-folder", {"path": "Nova pasta", "customer": CUSTOMER}, 1)],
        )
        self.assertEqual(self.advbox.held, 0)

    def test_an_existing_name_is_returned_and_nothing_is_created(self):
        result = self.documents.create_folder(CUSTOMER, "documentos")
        self.assertEqual((result["created"], result["existing"]), (False, True))
        self.assertEqual(result["folder"]["path"], "Documentos")
        self.assertEqual(self.advbox.kinds("/file/create-folder"), [])

    def test_dry_run_sends_nothing_even_without_the_write_gate(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
            new = self.documents.create_folder(CUSTOMER, "Nova", dry_run=True)
            old = self.documents.create_folder(CUSTOMER, "Documentos", dry_run=True)
        self.assertEqual(new["request"]["form"], {"path": "Nova", "customer": CUSTOMER})
        self.assertEqual((new["existing"], old["existing"], old["request"]), (False, True, None))
        self.assertEqual(self.advbox.kinds("/file/create-folder"), [])
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}), self.assertRaises(PermissionError):
            self.documents.create_folder(CUSTOMER, "Nova")

    def test_an_unanswered_creation_is_looked_up_never_replayed(self):
        self.advbox.create_failure = Timeout("no answer")  # the fake stores the folder, then the answer is lost
        result = self.documents.create_folder(CUSTOMER, "Perdida")
        self.assertEqual((result["created"], result["folder"]["path"]), (True, "Perdida"))

        self.advbox.create_stores = False  # this time it really was not stored
        with self.assertRaises(UnconfirmedWriteError) as caught:
            self.documents.create_folder(CUSTOMER, "Outra")
        self.assertIn("MAY have been created", str(caught.exception))
        self.assertIn("advbox_list_files", str(caught.exception))
        self.assertEqual(len(self.advbox.kinds("/file/create-folder")), 2)  # one POST per call

    def test_a_refusal_is_reported(self):
        self.advbox.create_answer = "<b>erro</b>"
        with self.assertRaisesRegex(RuntimeError, "did not create the folder Nova: erro"):
            self.documents.create_folder(CUSTOMER, "Nova")

    def test_names_and_paths(self):
        for bad in ("", "a/b", "a\\b", "..", ".", "x" * 41, "a\x00b", "%2e%2e"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                folder_name(bad)
        for bad in ("../x", "a/../b", "a/%2e%2e", "a//b", "a\\b"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                folder_path(bad)
        self.assertEqual(folder_path("/00-PROTOCOLAR/Anexos/"), "00-PROTOCOLAR/Anexos")


class UploadFiles(FolderCase):
    def test_one_file_is_stored_in_the_folder_and_confirmed_by_the_listing(self):
        result = self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR", [pdf("Procuração v1.2.pdf")])
        (entry,) = result["files"]
        self.assertEqual(entry["filename"], "Procuração v1.2.pdf")
        self.assertEqual(entry["storedName"], "Procuração v12_1762720000.pdf")
        self.assertEqual((entry["fileId"], entry["listed"]), ("50000001", True))
        self.assertEqual((result["uploaded"], result["warnings"]), (1, []))
        self.assertEqual(self.advbox.kinds("upload"), [("upload", "Procuração v1.2.pdf", 1)])

    def test_several_files_are_uploaded_one_request_each_under_one_lock(self):
        names = ["a.pdf", "b.pdf", "c.pdf"]
        result = self.documents.upload_files(CUSTOMER, "00-protocolar", [pdf(name) for name in names])
        self.assertEqual([item["filename"] for item in result["files"]], names)
        self.assertEqual(result["folder"], "00-PROTOCOLAR")  # the listed spelling is what is sent
        self.assertEqual([call[2] for call in self.advbox.kinds("upload")], [1, 1, 1])
        self.assertEqual(len(self.advbox.files["00-PROTOCOLAR"]), 3)
        self.assertTrue(all(item["listed"] for item in result["files"]))

    def test_a_refused_later_file_removes_the_ones_already_uploaded(self):
        self.advbox.multipart_failures[3] = RuntimeError("ADVBOX POST /s3 failed (422): <b>arquivo inválido</b>")
        with self.assertRaises(RuntimeError) as caught:
            self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR", [pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")])
        self.assertNotIsInstance(caught.exception, UnconfirmedWriteError)
        self.assertIn("upload of c.pdf into 00-PROTOCOLAR failed", str(caught.exception))
        self.assertIn("2 file(s) uploaded earlier in this call were removed", str(caught.exception))
        self.assertEqual([call[1]["id"] for call in self.advbox.kinds("/s3")], ["50000001", "50000002"])
        self.assertEqual(self.advbox.files["00-PROTOCOLAR"], [])

    def test_a_file_that_cannot_be_removed_is_named(self):
        self.advbox.multipart_failures[2] = RuntimeError("ADVBOX POST /s3 failed (413)")
        self.advbox.delete_failures.add("50000001")
        with self.assertRaisesRegex(RuntimeError, r"could not be removed .*a\.pdf \(fileId 50000001\)"):
            self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR", [pdf("a.pdf"), pdf("b.pdf")])

    def test_an_unanswered_upload_may_have_happened_and_nothing_is_replayed_or_removed(self):
        for failure in (Timeout("no answer"), RuntimeError("ADVBOX POST /s3 failed (502)"), "no-file"):
            with self.subTest(failure=failure):
                self.advbox = FakeAdvbox()
                self.documents = AdvboxDocuments(AdvboxClient(session=self.advbox))
                self.advbox.multipart_failures[2] = failure
                with self.assertRaises(UnconfirmedWriteError) as caught:
                    self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR", [pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")])
                message = str(caught.exception)
                self.assertIn("b.pdf: it MAY have been uploaded to 00-PROTOCOLAR", message)
                self.assertIn("advbox_list_files", message)
                self.assertIn("Uploaded and confirmed: a.pdf (fileId 50000001)", message)
                self.assertIn("Not sent: c.pdf", message)
                self.assertEqual(len(self.advbox.kinds("upload")), 2)
                self.assertEqual(self.advbox.kinds("/s3"), [])

    def test_missing_and_system_folders_are_refused_before_any_upload(self):
        with self.assertRaisesRegex(ValueError, "advbox_create_folder"):
            self.documents.upload_files(CUSTOMER, "Inexistente", [pdf("a.pdf")])
        with self.assertRaisesRegex(ValueError, "system folder"):
            self.documents.upload_files(CUSTOMER, "Anexado em tarefas", [pdf("a.pdf")])
        with self.assertRaisesRegex(ValueError, "Choose the folder"):
            self.documents.upload_files(CUSTOMER, "", [pdf("a.pdf")])
        with self.assertRaises(ValueError):
            self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR/../Documentos", [pdf("a.pdf")])
        self.assertEqual(self.advbox.kinds("upload"), [])

    def test_dry_run_checks_the_folder_and_sends_nothing(self):
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
            result = self.documents.upload_files(CUSTOMER, "00-PROTOCOLAR", [pdf("a.pdf")], dry_run=True)
        self.assertEqual(result["request"]["multipart"]["path_filter"], "00-PROTOCOLAR")
        self.assertEqual(result["files"][0]["bytes"], len(pdf("a.pdf")["bytes"]))
        self.assertEqual(self.advbox.kinds("upload"), [])

    def test_the_recorded_answer_names_one_stored_file(self):
        entry = parse_uploaded_file(file_item("50000009", "Laudo_1762720000.pdf"))
        self.assertEqual((entry["fileId"], entry["name"]), ("50000009", "Laudo_1762720000.pdf"))
        self.assertIsNone(parse_uploaded_file("success"))


class FolderTools(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = mock.patch.dict(os.environ, {"JURISYNC_DOCUMENTS_DIR": folder.name, "ADVBOX_ALLOW_WRITES": "true"})
        env.start()
        self.addCleanup(env.stop)
        self.advbox = FakeAdvbox()
        documents = mock.patch.object(server, "documents", AdvboxDocuments(AdvboxClient(session=self.advbox)))
        documents.start()
        self.addCleanup(documents.stop)
        (self.root / "procuracao.pdf").write_bytes(b"%PDF-1.4")
        (self.root / "minuta.docx").write_bytes(b"PK")

    def test_files_come_from_the_shared_documents_folder(self):
        result = server.upload_files(CUSTOMER, "00-PROTOCOLAR", ["procuracao.pdf", "minuta.docx"])
        self.assertEqual([item["filename"] for item in result["files"]], ["procuracao.pdf", "minuta.docx"])

    def test_paths_outside_the_shared_folder_are_refused(self):
        outside = self.root.parent / "fora.pdf"
        for name in ("../fora.pdf", str(outside), "sub/procuracao.pdf", ".env", "nada.pdf", "script.sh"):
            with self.subTest(name=name), self.assertRaises(Exception):
                server.upload_files(CUSTOMER, "00-PROTOCOLAR", [name])
        self.assertEqual(self.advbox.kinds("upload"), [])

    def test_create_folder_tool_and_write_gate(self):
        self.assertTrue(server.create_folder(CUSTOMER, "Nova")["created"])
        with mock.patch.dict(os.environ, {"ADVBOX_ALLOW_WRITES": "false"}):
            self.assertTrue(server.create_folder(CUSTOMER, "Outra", dryRun=True)["dryRun"])
            self.assertTrue(server.upload_files(CUSTOMER, "00-PROTOCOLAR", ["procuracao.pdf"], dryRun=True)["dryRun"])
            for write in (
                lambda: server.create_folder(CUSTOMER, "Outra"),
                lambda: server.upload_files(CUSTOMER, "00-PROTOCOLAR", ["procuracao.pdf"]),
            ):
                with self.assertRaises(Exception) as caught:
                    write()
                self.assertIn("ADVBOX_ALLOW_WRITES", str(caught.exception))

    def test_annotations(self):
        import asyncio

        from mcp.client.client import Client

        async def tools():
            async with Client(server.server) as client:
                return {tool.name: tool for tool in (await client.list_tools()).tools}

        listed = asyncio.run(tools())
        create, upload = listed["advbox_create_folder"], listed["advbox_upload_files"]
        self.assertEqual(
            (
                create.annotations.read_only_hint,
                create.annotations.destructive_hint,
                create.annotations.idempotent_hint,
            ),
            (False, False, True),
        )
        self.assertEqual(
            (
                upload.annotations.read_only_hint,
                upload.annotations.destructive_hint,
                upload.annotations.idempotent_hint,
            ),
            (False, False, False),
        )
        self.assertEqual(create.input_schema["required"], ["customerId", "name"])
        self.assertEqual(upload.input_schema["required"], ["customerId", "folder", "files"])


if __name__ == "__main__":
    unittest.main()
