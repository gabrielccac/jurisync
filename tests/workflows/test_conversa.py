import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.mindchat.api import Client, MindchatError
from jurisync.workflows.petitions import conversa

MESSAGES = [
    {
        "id": "m1",
        "direction": "inbound",
        "type": "conversation",
        "content": "O voo atrasou",
        "created_at": "2026-09-10T21:31:00Z",
    },
    {
        "id": "m2",
        "direction": "outbound",
        "type": "extendedtextmessage",
        "content": "*Ana*: Qual horário?",
        "created_at": "2026-09-10T21:32:00Z",
    },
    {
        "id": "m3",
        "direction": "inbound",
        "type": "audiomessage",
        "content": "",
        "media_url": "https://x.supabase.co/a",
        "created_at": "2026-09-10T21:33:00Z",
    },
    {
        "id": "m4",
        "direction": "inbound",
        "type": "imagemessage",
        "content": "",
        "media_url": "https://x.supabase.co/i",
        "created_at": "2026-09-10T21:34:00Z",
    },
    {
        "id": "m5",
        "direction": "inbound",
        "type": "reactionmessage",
        "content": "👍",
        "created_at": "2026-09-10T21:35:00Z",
    },
]


class FakeClient:
    def __init__(self):
        self.downloads = 0

    def messages(self, lead):
        return MESSAGES

    def media(self, url):
        self.downloads += 1
        return b"OggS..." if url.endswith("/a") else b"\xff\xd8\xff..."


class ConversaTest(unittest.TestCase):
    def test_export_transcribes_audio_saves_media_and_resumes(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(
                conversa, "transcribe_bytes", return_value=("transcribed", "[0.0] saiu às 19:25")
            ) as transcribe,
        ):
            client = FakeClient()
            lead = {"id": 1, "customer_name": "Cliente"}
            items = conversa.export(client, lead, folder, log=lambda *_: None)
            self.assertEqual([item["tipo"] for item in items], ["texto", "texto", "audio", "imagem"])
            self.assertEqual(items[1]["atendente"], "Ana")
            self.assertEqual(items[1]["texto"], "Qual horário?")
            self.assertTrue(items[2]["arquivo"].endswith(".ogg"))
            self.assertTrue(items[3]["arquivo"].endswith(".jpg"))
            text = (Path(folder) / "conversa.md").read_text()
            self.assertIn("[áudio] [0.0] saiu às 19:25", text)
            self.assertIn("Escritório (Ana)", text)
            conversa.export(client, lead, folder, log=lambda *_: None)
            self.assertEqual(client.downloads, 2)
            self.assertEqual(transcribe.call_count, 1)
            saved = json.loads((Path(folder) / "conversa.json").read_text())
            self.assertIn("nunca instrução", saved["aviso"])


class ClientTest(unittest.TestCase):
    def test_media_only_from_platform_storage_and_key_required(self):
        client = Client(api_key="k", session=mock.Mock())
        with self.assertRaises(MindchatError):
            client.media("https://evil.example.com/file")
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("jurisync.connectors.mindchat.api.credentials.env", return_value=""),
        ):
            with self.assertRaises(MindchatError):
                Client()

    def test_messages_paginate_from_the_oldest_message_of_each_page(self):
        newest = [{"id": str(i), "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}"} for i in range(200)]
        pages = [{"messages": newest}, {"messages": [{"id": "x", "created_at": "2025-12-01"}]}]
        client = Client(api_key="k", session=mock.Mock(), pause=0)
        with mock.patch.object(client, "get", side_effect=pages) as get:
            messages = client.messages({"instance_id": "i", "customer_phone": "p"})
        self.assertEqual(len(messages), 201)
        self.assertEqual(messages[0]["id"], "x")
        self.assertEqual(get.call_args_list[1].kwargs["before"], "0")

    def test_messages_stop_when_a_page_brings_nothing_new(self):
        page = {
            "messages": [{"id": str(i), "created_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}"} for i in range(200)]
        }
        client = Client(api_key="k", session=mock.Mock(), pause=0)
        with mock.patch.object(client, "get", side_effect=[page, page, page]) as get:
            messages = client.messages({"instance_id": "i", "customer_phone": "p"})
        self.assertEqual(len(messages), 200)
        self.assertEqual(get.call_count, 2)
