"""Juridiq REST client: cached bearer session and named operations.

The bearer token is cached per account in a private file and refreshed once
on a 401. Writes require JURIDIQ_ALLOW_WRITES=true; the
POST /generic-tables query is a read.
"""

import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlsplit

from curl_cffi import requests

MAX_PAGINATION_PAGES = 10_000


class JuridiqError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status, self.payload = status, payload


def _cache_dir():
    """Private token cache outside the repository (LocalAppData on Windows)."""
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Juridiq"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "jurisync" / "juridiq"


def config():
    email = (os.environ.get("JURIDIQ_EMAIL") or "").strip().lower()
    name = f"bearer-{hashlib.sha256(email.encode()).hexdigest()[:16]}.json" if email else "bearer-session.json"
    return {
        "apiBaseUrl": os.environ.get("JURIDIQ_API_BASE_URL") or "https://api.juridiq.com.br",
        "loginUrl": os.environ.get("JURIDIQ_LOGIN_URL") or "https://www.api.juridiq.com.br/auth/login",
        "email": email,
        "password": os.environ.get("JURIDIQ_PASSWORD"),
        "sessionFile": Path(os.environ.get("JURIDIQ_SESSION_FILE") or _cache_dir() / name),
    }


def jwt_expiry(token):
    try:
        segment = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and not isinstance(exp, bool):
            return datetime.fromtimestamp(exp, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (IndexError, ValueError, TypeError):
        pass
    return None


def is_fresh(session, skew_seconds=60):
    if not (session or {}).get("token"):
        return False
    if not session.get("expiresAt"):
        return True
    try:
        expires = datetime.fromisoformat(session["expiresAt"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return (expires - datetime.now(UTC)).total_seconds() > skew_seconds


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


def _value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(_value(item) for item in value)
    return str(value)


def _query(base, **values):
    """Build a query string like URLSearchParams, skipping empty values."""
    pairs = [(name, _value(value)) for name, value in base]
    pairs += [(name, _value(value)) for name, value in values.items() if value not in (None, "")]
    return urlencode(pairs)


def _segment(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return quote(value, safe="!'()*-._~")


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def payload_data(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _send(method, url, headers, body):
    with requests.Session() as session:
        return session.request(method, url, headers=headers, data=body, allow_redirects=False, timeout=60)


class JuridiqApi:
    def __init__(self, send=_send):
        self._send = send

    # -- session -------------------------------------------------------------

    def _login(self, settings):
        if not settings["email"] or not settings["password"]:
            raise JuridiqError("Set JURIDIQ_EMAIL/JURIDIQ_PASSWORD.")
        response = self._send(
            "POST",
            settings["loginUrl"],
            {"accept": "application/json", "content-type": "application/json"},
            json.dumps({"email": settings["email"], "password": settings["password"]}),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        token = payload.get("token") if isinstance(payload, dict) else None
        if not 200 <= response.status_code < 300 or not isinstance(token, str):
            message = payload.get("message") if isinstance(payload, dict) else None
            raise JuridiqError(message or f"Juridiq login failed ({response.status_code}).", response.status_code)
        path = settings["sessionFile"]
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "token": token,
                        "baseUrl": settings["apiBaseUrl"],
                        "expiresAt": jwt_expiry(token),
                        "updatedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    },
                    indent=2,
                )
                + "\n"
            )
        return token

    def token(self, force_refresh=False):
        settings = config()
        try:
            cached = json.loads(settings["sessionFile"].read_text(encoding="utf-8"))
        except FileNotFoundError:
            cached = None
        if (
            not force_refresh
            and isinstance(cached, dict)
            and cached.get("baseUrl") == settings["apiBaseUrl"]
            and is_fresh(cached)
        ):
            return cached["token"]
        return self._login(settings)

    def request(self, path, method="GET", body=None):
        settings = config()
        url = urljoin(settings["apiBaseUrl"], path)
        parts = urlsplit(url)
        if _origin(url) != _origin(settings["apiBaseUrl"]) or parts.username or parts.password:
            raise JuridiqError("Juridiq requests must use the configured API origin.")
        method = method.upper()
        read_query = method == "POST" and parts.path == "/generic-tables"
        if method not in ("GET", "HEAD") and not read_query and os.environ.get("JURIDIQ_ALLOW_WRITES") != "true":
            raise PermissionError(
                "Juridiq writes are disabled. Set JURIDIQ_ALLOW_WRITES=true after confirming the operation."
            )
        token = self.token()
        for attempt in range(2):
            headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
            if body is not None:
                headers["content-type"] = "application/json"
            response = self._send(method, url, headers, None if body is None else json.dumps(body))
            if 300 <= response.status_code < 400:
                raise JuridiqError(f"Juridiq {method} {path} redirected unexpectedly", response.status_code)
            if response.status_code != 401 or attempt == 1:
                return response
            settings["sessionFile"].unlink(missing_ok=True)
            token = self.token(force_refresh=True)

    def json(self, path, method="GET", body=None):
        response = self.request(path, method, body)
        text = response.text
        try:
            payload = json.loads(text) if text else None
        except ValueError as error:
            if 200 <= response.status_code < 300:
                return text
            raise JuridiqError(f"Juridiq returned non-JSON for {path}: {error}", response.status_code) from None
        if not 200 <= response.status_code < 300:
            details = (
                ""
                if payload is None
                else f": {payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)}"
            )
            raise JuridiqError(
                f"Juridiq {method} {path} failed ({response.status_code}){details}", response.status_code, payload
            )
        return payload

    # -- publications --------------------------------------------------------

    def publication_page(
        self,
        *,
        page=1,
        limit=50,
        unreadOnly=True,
        isHandled=None,
        orderBy=None,
        keySearch=None,
        courtOrDiary=None,
        monitoredTermId=None,
        start=None,
        end=None,
    ):
        handled = isHandled if isHandled is not None else (False if unreadOnly else None)
        query = _query(
            [("page", page), ("limit", limit)],
            orderBy=orderBy,
            keySearch=keySearch,
            courtOrDiary=courtOrDiary,
            isHandled=handled,
            monitoredTermId=monitoredTermId,
            start=start,
            end=end,
        )
        return self.json(f"/publication?{query}")

    def _all_pages(self, fetch_page, *, limit=100, maxResults=None, resource, select=lambda item: True):
        size = min(100, int(limit)) if limit and limit >= 1 else None
        if size is None:
            raise ValueError("limit must be a positive number")
        maximum = float("inf") if maxResults is None else int(maxResults)
        results, ids, page = [], set(), 1
        while len(results) < maximum:
            if page > MAX_PAGINATION_PAGES:
                raise JuridiqError(f"Juridiq {resource} pagination exceeded {MAX_PAGINATION_PAGES} pages.")
            batch = fetch_page(page, size)
            for item in batch:
                if not isinstance(item, dict) or not item.get("id") or item["id"] in ids:
                    raise JuridiqError(f"Juridiq {resource} pagination returned duplicate/invalid IDs.")
                ids.add(item["id"])
                if not select(item):
                    continue
                results.append(item)
                if len(results) == maximum:
                    break
            if len(batch) < size:
                break
            page += 1
        return results

    def all_unread_publications(self, *, limit=100, maxResults=None):
        def unread(item):
            return item.get("readAt") is None and item.get("isHandled") is not True

        return self._all_pages(
            lambda page, size: payload_data(self.publication_page(page=page, limit=size)),
            limit=limit,
            maxResults=maxResults,
            resource="publication",
            select=unread,
        )

    def all_publications(self, *, limit=100, maxResults=None):
        return self._all_pages(
            lambda page, size: payload_data(self.publication_page(page=page, limit=size, unreadOnly=False)),
            limit=limit,
            maxResults=maxResults,
            resource="publication",
        )

    def get_publication(self, publication_id):
        return self.json(f"/publication/{_segment(publication_id, 'publicationId')}")

    def mark_publications_read(self, ids):
        if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError("publication IDs must be a nonempty array of strings")
        return self.json("/publication/read-movements-many", "PATCH", {"ids": ids})

    # -- lawsuits ------------------------------------------------------------

    def find_lawsuits(self, process_number, *, page=1, limit=50):
        if not isinstance(process_number, str) or not process_number.strip():
            raise ValueError("processNumber is required")
        return payload_data(
            self.json(
                f"/lawSuit?{_query([('page', page), ('limit', limit), ('processNumber', process_number.strip())])}"
            )
        )

    def find_exact_lawsuit(self, process_number, **options):
        exact = [
            item
            for item in self.find_lawsuits(process_number, **options)
            if isinstance(item, dict) and item.get("processNumber") == process_number
        ]
        if len(exact) > 1:
            raise JuridiqError(f"Juridiq returned multiple exact lawSuit matches for {process_number}.")
        return exact[0] if exact else None

    def all_lawsuits(self, *, limit=100, maxResults=None):
        return self._all_pages(
            lambda page, size: payload_data(self.json(f"/lawSuit?{_query([('page', page), ('limit', size)])}")),
            limit=limit,
            maxResults=maxResults,
            resource="lawsuit",
        )

    def get_lawsuit(self, lawsuit_id):
        return self.json(f"/lawSuit/{_segment(lawsuit_id, 'lawSuitId')}")

    def lawsuit_movements(self, lawsuit_id, *, tagId=None, content=None, instance=None):
        query = _query([], tagId=tagId, content=content, instance=instance)
        return self.json(f"/lawSuit/movements/{_segment(lawsuit_id, 'lawSuitId')}{'?' + query if query else ''}")

    def create_lawsuit(self, payload):
        return self.json("/lawSuit/", "POST", _object(payload, "lawSuit payload"))

    def update_lawsuit(self, lawsuit_id, patch):
        return self.json(f"/lawSuit/{_segment(lawsuit_id, 'lawSuitId')}", "PATCH", _object(patch, "lawSuit patch"))

    def delete_lawsuit(self, lawsuit_id):
        _segment(lawsuit_id, "lawSuitId")
        return self.json("/lawSuit/", "DELETE", {"id": [lawsuit_id]})

    # -- office --------------------------------------------------------------

    def task_templates(self, *, page=1, limit=100):
        return payload_data(self.json(f"/task/task-templates?page={page}&limit={limit}"))

    def users(self, *, page=1, limit=100):
        return payload_data(self.json(f"/user/?page={page}&limit={limit}"))

    def tags(self, *, page=1, limit=100):
        return payload_data(self.json(f"/tag/?page={page}&limit={limit}"))

    def task_columns(self, *, page=1, limit=100):
        payload = self.json(f"/tasks-columns/?page={page}&limit={limit}&isSettings=true")
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                return payload["data"]
            columns = payload.get("columns")
            if isinstance(columns, list):
                return columns
            if isinstance(columns, dict):
                return list(columns.values())
        return []

    # -- tasks and events ----------------------------------------------------

    @staticmethod
    def _task_query(page=1, limit=30, lawSuitId=None, **filters):
        order = (
            "title",
            "responsible",
            "priority",
            "isHome",
            "orderBy",
            "orderByField",
            "isUniqueResponsible",
            "isArchived",
            "isPrivate",
            "start",
            "end",
            "tag",
            "dateMethod",
            "personId",
            "points",
            "parentTaskId",
            "taskColumnId",
            "folderId",
            "boardId",
            "taskKanbanColumnIds",
            "status",
        )
        unknown = set(filters) - set(order)
        if unknown:
            raise ValueError(f"Unknown task filters: {sorted(unknown)}")
        return _query(
            [("page", page), ("limit", limit)], **{name: filters.get(name) for name in order}, lawSuit=lawSuitId
        )

    def tasks(self, **options):
        return payload_data(self.json(f"/task/?{self._task_query(**options)}"))

    def kanban_tasks(self, **options):
        return self.json(f"/task/kanban?{self._task_query(**options)}")

    def get_task(self, task_id):
        return self.json(f"/task/{_segment(task_id, 'taskId')}")

    def task_history(self, task_id):
        return self.json(f"/history/{_segment(task_id, 'taskId')}")

    def task_comments(self, task_id):
        return self.json(f"/comment/?taskId={_segment(task_id, 'taskId')}")

    def create_task(self, payload):
        return self.json("/task/", "POST", _object(payload, "task payload"))

    def update_task(self, task_id, patch):
        return self.json(f"/task/{_segment(task_id, 'taskId')}", "PATCH", _object(patch, "task patch"))

    def delete_task(self, task_id):
        _segment(task_id, "taskId")
        return self.json("/task/", "DELETE", {"id": [task_id]})

    def events(self, *, page=1, limit=50, **filters):
        order = (
            "responsible",
            "responsiblesMetrics",
            "personId",
            "lawSuitId",
            "dateType",
            "start",
            "end",
            "statusFilter",
            "fullQuery",
            "includeFutureCount",
            "tag",
            "itemType",
            "taskPriority",
            "taskTag",
            "orderBy",
            "orderByField",
        )
        unknown = set(filters) - set(order)
        if unknown:
            raise ValueError(f"Unknown event filters: {sorted(unknown)}")
        return self.json(
            f"/event/?{_query([('page', page), ('limit', limit)], **{name: filters.get(name) for name in order})}"
        )

    def get_event(self, event_id):
        return self.json(f"/event/{_segment(event_id, 'eventId')}")

    def create_event(self, payload):
        return self.json("/event/", "POST", _object(payload, "event payload"))
