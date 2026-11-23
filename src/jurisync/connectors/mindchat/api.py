"""MindChat (MindIA) REST client, read-only: leads, notes, conversation messages and media.

Authentication is the account's API key in the ``X-Api-Key`` header (``MINDCHAT_API_KEY`` in the customer's
``.env``). Only GET endpoints are wrapped: sending messages, notes, tags or CRM changes is outside this client.
A conversation and its notes are addressed by the contact's phone, which the API takes as a query parameter.
"""

import time
from urllib.parse import urlsplit

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from jurisync.core import credentials

BASE_URL = "https://api.appmindchat.com.br/webhook/v1"
TIMEOUT = 40
PAGE = 100
MESSAGES_PAGE = 200
MAX_PAGES = 500
# Media come from the platform's public storage; anything else is not downloaded.
MEDIA_HOSTS = (".supabase.co", "appmindchat.com.br")


class MindchatError(RuntimeError):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


class Client:
    def __init__(self, api_key=None, base_url=None, session=None, pause=0.3):
        self.api_key = api_key or (credentials.env("MINDCHAT_API_KEY") or "").strip()
        if not self.api_key:
            raise MindchatError("MINDCHAT_API_KEY is not set for this customer")
        self.base_url = (base_url or credentials.env("MINDCHAT_BASE_URL") or BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.pause = pause

    def get(self, endpoint, **params):
        params = {key: value for key, value in params.items() if value not in (None, "")}
        try:
            response = self.session.get(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                params=params,
                headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
        except RequestException as error:
            raise MindchatError(f"MindChat request failed: {type(error).__name__}") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise MindchatError(f"MindChat answered HTTP {response.status_code} without JSON") from error
        if response.status_code != 200 or not payload.get("success"):
            raise MindchatError(
                f"MindChat {endpoint}: {payload.get('code') or response.status_code}: {payload.get('message') or ''}",
                status=response.status_code,
                code=payload.get("code"),
            )
        return payload.get("data")

    def account(self):
        return self.get("account/me")

    def stages(self):
        data = self.get("leads/crm-stage")
        items = data.get("items") if isinstance(data, dict) else data
        return {item["stage_id"]: item["name"].strip() for item in items or []}

    def leads(self, **filters):
        """Every lead matching the filters (name, phone, cpf, crm_stage_id, tag_id, created_after, ...)."""
        for page in range(1, MAX_PAGES + 1):
            data = self.get("leads/list", per_page=PAGE, page=page, **filters)
            items = data.get("items", []) if isinstance(data, dict) else data or []
            yield from items
            if len(items) < PAGE:
                return
            time.sleep(self.pause)

    def notes(self, lead):
        data = self.get(
            "leads/notes", instance_id=lead["instance_id"], customer_phone=lead["customer_phone"], lead_id=lead["id"]
        )
        return (data or {}).get("notes") or []

    def messages(self, lead):
        """The whole conversation, oldest first."""
        collected, before = [], None
        for _ in range(MAX_PAGES):
            data = self.get(
                "conversations/messages",
                instance_id=lead["instance_id"],
                customer_phone=lead["customer_phone"],
                limit=MESSAGES_PAGE,
                before=before,
            )
            page = (data or {}).get("messages") or []
            unique = {message["id"]: message for message in collected}
            fresh = [message for message in page if message["id"] not in unique]
            collected += fresh
            if len(page) < MESSAGES_PAGE or not fresh:
                break
            # A page holds the newest messages before the cursor, oldest first: go on from its oldest one.
            before = min(page, key=lambda message: message.get("created_at") or "")["id"]
            time.sleep(self.pause)
        return sorted(collected, key=lambda message: message.get("created_at") or "")

    def media(self, url):
        """Bytes of a conversation attachment from the platform's storage."""
        parts = urlsplit(url or "")
        if parts.scheme != "https" or not any(parts.hostname and parts.hostname.endswith(host) for host in MEDIA_HOSTS):
            raise MindchatError("Media URL is not from MindChat storage")
        try:
            response = self.session.get(url, timeout=TIMEOUT)
        except RequestException as error:
            raise MindchatError(f"Media download failed: {type(error).__name__}") from error
        if response.status_code != 200:
            raise MindchatError(f"Media download failed (HTTP {response.status_code})", status=response.status_code)
        return response.content
