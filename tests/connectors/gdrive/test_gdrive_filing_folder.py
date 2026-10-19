import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jurisync.connectors.gdrive.auth import SCOPE, DriveAuthError, ServiceAccount, load_key, signed_assertion
from jurisync.connectors.gdrive.client import DriveClient, DriveError, TooLarge
from jurisync.connectors.gdrive.filing_folder import check_access, drive_id, export_filing_folder, folder_id
from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.contract import validate_folder
from jurisync.workflows.filing.inbox import fill_from_folder

PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY = {
    "type": "service_account",
    "client_email": "jurisync@office.iam.gserviceaccount.com",
    "private_key_id": "k1",
    "private_key": PRIVATE.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode(),
    "token_uri": "https://oauth2.googleapis.com/token",
}
ROOT = "ROOTFOLDER0001"
PDF, DOC_PDF, BIG = b"%PDF-1.4 laudo", b"%PDF-1.4 exported doc", b"%PDF-1.4 " + b"x" * 3000


def md5(content):
    return hashlib.md5(content).hexdigest()


class FakeDrive:
    """Google's token endpoint and Drive v3 as the exporter calls them."""

    def __init__(self):
        self.calls = []
        self.items = {
            ROOT: {"id": ROOT, "name": "Maria - BPC", "mimeType": "application/vnd.google-apps.folder"},
            "SUBFOLDER0001": {
                "id": "SUBFOLDER0001",
                "name": "Laudos",
                "mimeType": "application/vnd.google-apps.folder",
            },
            "INICIAIS00001": {
                "id": "INICIAIS00001",
                "name": "1. Iniciais - BPC (Controladoria)",
                "mimeType": "application/vnd.google-apps.folder",
            },
        }
        self.items["PETITION0001"] = self.file(
            "PETITION0001", "Inicial BPC - Maria.pdf", PDF + b"p", parents=["INICIAIS00001"]
        )
        self.pages = {
            ROOT: [
                [
                    self.file("FILE00000001", "10-Petição inicial.pdf", PDF),
                    {"id": "SUBFOLDER0001", "name": "Laudos", "mimeType": "application/vnd.google-apps.folder"},
                ],
                [
                    {
                        "id": "GDOC00000001",
                        "name": "Procuração",
                        "mimeType": "application/vnd.google-apps.document",
                        "modifiedTime": "2026-10-01T10:00:00Z",
                        "webViewLink": "https://docs.google.com/document/d/GDOC00000001",
                    },
                    {"id": "FORM00000001", "name": "Questionário", "mimeType": "application/vnd.google-apps.form"},
                ],
            ],
            "SUBFOLDER0001": [[self.file("FILE00000002", "Laudo CAPS.pdf", PDF + b"2")]],
        }
        self.content = {
            "FILE00000001": PDF,
            "FILE00000002": PDF + b"2",
            "GDOC00000001": DOC_PDF,
            "PETITION0001": PDF + b"p",
        }

    @staticmethod
    def file(file_id, name, content, **extra):
        return {
            "id": file_id,
            "name": name,
            "mimeType": "application/pdf",
            "size": str(len(content)),
            "md5Checksum": md5(content),
            "modifiedTime": "2026-10-02T12:00:00Z",
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            **extra,
        }

    def __call__(self, method, url, headers=None, data=None, sink=None, limit=None):
        self.calls.append((method, url))
        parts = urlsplit(url)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if method == "POST":
            form = {key: values[0] for key, values in parse_qs(data.decode()).items()}
            assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
            return 200, {}, json.dumps({"access_token": "token-1", "expires_in": 3600}).encode()
        assert headers["Authorization"] == "Bearer token-1"
        path = unquote(parts.path).removeprefix("/drive/v3/files")
        if path == "":
            folder = query["q"].split("'")[1]
            assert query["supportsAllDrives"] == query["includeItemsFromAllDrives"] == "true"
            pages = self.pages[folder]
            index = int(query.get("pageToken", "0"))
            body = {"files": pages[index]}
            if index + 1 < len(pages):
                body["nextPageToken"] = str(index + 1)
            return 200, {}, json.dumps(body).encode()
        file_id = path.strip("/").removesuffix("/export")
        if path.endswith("/export"):
            assert query["mimeType"] == "application/pdf"
            content = self.content[file_id]
        elif query.get("alt") == "media":
            content = self.content[file_id]
        else:
            if file_id not in self.items:
                return 404, {}, b'{"error": {"code": 404, "message": "File not found"}}'
            return 200, {}, json.dumps(self.items[file_id]).encode()
        if limit is not None and len(content) > limit:
            raise TooLarge(f"more than {limit} bytes")
        sink.write(content)
        return 200, {}, b""


def client(drive=None):
    drive = drive or FakeDrive()
    return DriveClient(ServiceAccount(drive, KEY), drive), drive


class ServiceAccountTests(unittest.TestCase):
    def test_the_assertion_asks_only_for_read_only_drive_and_is_signed_by_the_key(self):
        token = signed_assertion(KEY, now=1_000_000)
        header, claims, signature = token.split(".")
        decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        body = json.loads(decode(claims))
        self.assertEqual((body["scope"], body["iss"], body["exp"] - body["iat"]), (SCOPE, KEY["client_email"], 3600))
        self.assertTrue(SCOPE.endswith("drive.readonly"))
        PRIVATE.public_key().verify(
            decode(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )

    def test_the_key_must_exist_be_private_and_be_a_service_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "key.json")
            with mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_FILE": str(path)}):
                with self.assertRaisesRegex(
                    DriveAuthError, "No Google service-account key.*GOOGLE_SERVICE_ACCOUNT_FILE"
                ):
                    load_key()
                path.write_text(json.dumps(KEY))
                path.chmod(0o644)
                with self.assertRaisesRegex(DriveAuthError, "chmod 600"):
                    load_key()
                path.chmod(0o600)
                self.assertEqual(load_key()["client_email"], KEY["client_email"])
                path.write_text(json.dumps({**KEY, "type": "authorized_user"}))
                with self.assertRaisesRegex(DriveAuthError, "not a Google service-account key"):
                    load_key()

    def test_folder_ids_come_from_ids_or_urls(self):
        self.assertEqual(folder_id("https://drive.google.com/drive/folders/1AbCdEfGhIjK?usp=sharing"), "1AbCdEfGhIjK")
        self.assertEqual(folder_id("https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjK"), "1AbCdEfGhIjK")
        self.assertEqual(
            drive_id("https://drive.google.com/file/d/1XyZ0123456/view?usp=drive_link", "file"), "1XyZ0123456"
        )
        self.assertEqual(folder_id("https://drive.google.com/open?id=1AbCdEfGhIjK"), "1AbCdEfGhIjK")
        with self.assertRaises(ValueError):
            folder_id("not a folder")


class ExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_a_folder_becomes_a_contract_folder_with_drive_origins(self):
        drive_client, drive = client()
        result = export_filing_folder(drive_client, ROOT, self.tmp / "out", include_subfolders=True)
        folder = Path(result["sourceFolder"])
        checked = validate_folder(folder)
        self.assertEqual(checked["files"], ["10-Petição inicial.pdf", "Laudos/Laudo CAPS.pdf", "Procuração.pdf"])
        laudo = checked["sources"]["Laudos/Laudo CAPS.pdf"]
        self.assertEqual(
            {key: laudo[key] for key in ("origin", "originId", "originPath", "originalName", "verifiedBy")},
            {
                "origin": "drive",
                "originId": "FILE00000002",
                "originPath": "Maria - BPC/Laudos",
                "originalName": "Laudo CAPS.pdf",
                "verifiedBy": "jurisync.gdrive",
            },
        )
        self.assertEqual(laudo["link"], "https://drive.google.com/file/d/FILE00000002/view")
        self.assertEqual((folder / "Procuração.pdf").read_bytes(), DOC_PDF, "Google Doc exported as PDF")
        self.assertEqual(
            result["skipped"],
            [{"name": "Questionário", "reason": "cannot be exported (application/vnd.google-apps.form)"}],
        )
        self.assertEqual(sum(1 for method, url in drive.calls if "pageToken=1" in url), 1, "second page was read")
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((folder / "Laudos" / "Laudo CAPS.pdf").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("Authorization", json.dumps(result))
        store = LocalRunStore(self.tmp / "run").ensure()
        inbox = fill_from_folder(store, folder)
        self.assertEqual({entry["origin"]["kind"] for entry in inbox["files"]}, {"exported"})

    def test_a_petition_from_another_folder_joins_the_client_folder(self):
        drive_client, _ = client()
        petition = "https://drive.google.com/file/d/PETITION0001/view"
        result = export_filing_folder(drive_client, ROOT, self.tmp / "out", files=[petition], exclude=["Procura*"])
        checked = validate_folder(Path(result["sourceFolder"]))
        self.assertEqual(checked["files"], ["10-Petição inicial.pdf", "Inicial BPC - Maria.pdf"])
        source = checked["sources"]["Inicial BPC - Maria.pdf"]
        self.assertEqual(
            (source["originPath"], source["originId"]), ("1. Iniciais - BPC (Controladoria)", "PETITION0001")
        )
        self.assertEqual(result["inputFolder"], "out")
        only = export_filing_folder(drive_client, None, self.tmp / "only", files=[petition])
        self.assertNotIn("driveFolder", only)
        with self.assertRaisesRegex(ValueError, "is a folder: pass it as `folder`"):
            export_filing_folder(drive_client, None, self.tmp / "x", files=[ROOT])
        with self.assertRaisesRegex(ValueError, "folder link, file links, or both"):
            export_filing_folder(drive_client, None, self.tmp / "y")

    def test_subfolders_and_excluded_names_stay_out_unless_asked(self):
        drive_client, _ = client()
        result = export_filing_folder(drive_client, ROOT, self.tmp / "out", exclude=["Procura*"])
        self.assertEqual([item["name"] for item in result["files"]], ["10-Petição inicial.pdf"])
        reasons = {item["name"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(reasons["Laudos"], "subfolder (includeSubfolders off)")
        self.assertEqual(reasons["Procuração"], "excluded")

    def test_a_download_that_differs_from_drives_md5_fails_and_leaves_nothing(self):
        drive = FakeDrive()
        drive.content["FILE00000001"] = PDF + b" corrupted"
        drive_client, _ = client(drive)
        with self.assertRaisesRegex(DriveError, "md5 mismatch"):
            export_filing_folder(drive_client, ROOT, self.tmp / "out")
        self.assertFalse((self.tmp / "out").exists())

    def test_a_folder_not_shared_with_the_account_says_whom_to_share_with(self):
        drive_client, _ = client()
        with self.assertRaisesRegex(DriveError, "not shared with the service account jurisync@office.*Viewer"):
            export_filing_folder(drive_client, "NOTSHARED0001", self.tmp / "out")
        with self.assertRaisesRegex(DriveError, "not shared"):
            check_access(drive_client, "NOTSHARED0001")
        access = check_access(drive_client, ROOT)
        self.assertEqual(access["folder"]["name"], "Maria - BPC")
        self.assertIn("jurisync@office.iam.gserviceaccount.com as Viewer", access["shareWith"])
        disabled = lambda *args, **kwargs: (403, {}, b'{"error": {"reason": "accessNotConfigured"}}')
        with self.assertRaisesRegex(DriveError, "Drive API is not enabled"):
            DriveClient(drive_client.account, disabled).metadata(ROOT)

    def test_files_over_the_limit_are_refused_before_or_while_downloading(self):
        drive = FakeDrive()
        drive.pages[ROOT][0].append(drive.file("BIGFILE00001", "Processo.pdf", BIG))
        drive.content["BIGFILE00001"] = BIG
        drive_client, _ = client(drive)
        with self.assertRaisesRegex(TooLarge, "Processo.pdf has 0.0 MB, more than the 0.002 MB limit"):
            export_filing_folder(drive_client, ROOT, self.tmp / "out", max_mb=0.002)
        del drive.pages[ROOT][0][-1]["size"], drive.pages[ROOT][0][-1]["md5Checksum"]
        with self.assertRaisesRegex(TooLarge, "Processo.pdf is larger than"):
            export_filing_folder(drive_client, ROOT, self.tmp / "out2", max_mb=0.002)
        self.assertFalse((self.tmp / "out2").exists())

    def test_never_inside_a_synced_folder_or_a_folder_with_files(self):
        drive_client, _ = client()
        with self.assertRaisesRegex(ValueError, "synced folder"):
            export_filing_folder(drive_client, ROOT, self.tmp / "Library/CloudStorage/GoogleDrive-x/out")
        busy = self.tmp / "busy"
        busy.mkdir()
        (busy / "a.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            export_filing_folder(drive_client, ROOT, busy)


if __name__ == "__main__":
    unittest.main()


class ServerTests(unittest.TestCase):
    def test_tools_and_a_clear_error_without_a_key(self):
        import asyncio

        from mcp.client.client import Client

        from jurisync.connectors.gdrive.server import server

        async def run():
            async with Client(server) as connection:
                tools = {tool.name: tool.annotations.read_only_hint for tool in (await connection.list_tools()).tools}
                result = await connection.call_tool("drive_check_access", {})
                return tools, result

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"GOOGLE_SERVICE_ACCOUNT_FILE": str(Path(tmp, "missing.json"))}),
        ):
            tools, result = asyncio.run(run())
        self.assertEqual(tools, {"drive_export_filing_folder": False, "drive_check_access": True})
        self.assertTrue(result.is_error)
        self.assertIn("No Google service-account key", result.content[0].text)
