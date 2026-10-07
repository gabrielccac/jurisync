"""Read-only INSS PAT operations through an authenticated GERID browser page."""

import asyncio
import base64
import binascii
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urlencode
from zoneinfo import ZoneInfo

from jurisync.connectors.inss.auth import default_profile, jwt_claims, login_and_authorize, qr_secret
from jurisync.core.pdf import attachment_mime_type, available_path, extract_pdf_metadata

TASKS_URL = "https://atendimento.inss.gov.br/tarefas"
DIGITS = re.compile(r"^\d+$")
AUTH_SCRIPT = "localStorage.getItem('ifs_auth') ?? localStorage.getItem('gerid_token')"


def _log(message):
    print(message, flush=True)  # stdout is diverted to stderr while the MCP server runs.


def _digits(value, name):
    if not isinstance(value, str) or not DIGITS.fullmatch(value):
        raise ValueError(f"{name} must contain only digits")
    return value


def attachment_filename(content_disposition, fallback):
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition or "", re.I)
    plain = re.search(r'filename="?([^";]+)"?', content_disposition or "", re.I)
    raw = content_disposition if content_disposition and ";" not in content_disposition else None
    name = (encoded and encoded[1]) or (plain and plain[1]) or raw or fallback
    try:
        name = unquote(name, errors="strict")
    except UnicodeDecodeError:
        pass
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.basename(name)).strip()
    return name if name and name not in (".", "..") else fallback


def process_report_request(protocolo, auth):
    _digits(protocolo, "protocolo")
    try:
        claims = jwt_claims(auth)
    except (ValueError, IndexError):
        raise ValueError("GERID credential is not a readable JWT") from None
    cpf = re.sub(r"\D", "", str(claims.get("cpf") or claims.get("user_name") or "")).zfill(11)
    nome = str(claims.get("nome") or "").strip()
    if not re.fullmatch(r"\d{11}", cpf) or not nome:
        raise ValueError("GERID credential does not contain CPF and name")
    query = urlencode({"protocolo": protocolo, "sistema": "PAT", "cpf": cpf, "nome": nome, "async": "false"})
    return f"/apis/consolidadorECApi/pap/metadados?{query}"


def _sao_paulo_date(moment):
    return moment.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%dT03:00:00.000Z")


def _search_date(value, fallback):
    if not value:
        return _sao_paulo_date(fallback)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T03:00:00.000Z"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid INSS search date: {value}") from None
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def task_search_request(page=1, pageSize=20, token="", now=None, **filters):
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if isinstance(pageSize, bool) or not isinstance(pageSize, int) or not 1 <= pageSize <= 100:
        raise ValueError("pageSize must be between 1 and 100")
    now = now or datetime.now(UTC)
    return {
        "path": f"/apis/requerimentosPortalApi/requerimento/ec/tarefa/consulta?first={(page - 1) * pageSize}&pageSize={pageSize}",
        "body": {
            "cpf": filters.get("cpf") or "",
            "nome": filters.get("nome") or "",
            "protocolo": filters.get("protocolo") or "",
            "status": filters.get("status") or "",
            "tokenReCAPTCHA": token,
            "dataAtualizacaoInicial": _search_date(filters.get("dataAtualizacaoInicial"), now - timedelta(days=180)),
            "dataAtualizacaoFinal": _search_date(filters.get("dataAtualizacaoFinal"), now),
            "situacaoTarefa": filters.get("situacaoTarefa") or "",
        },
    }


def save_base64_download(data, requested_name, fallback, expected_mime_type=None, directory=None):
    if not isinstance(data, str) or not data.strip():
        raise ValueError("INSS download response was empty or invalid Base64")
    try:
        content = base64.b64decode(re.sub(r"\s+", "", data))
    except binascii.Error:
        raise ValueError("INSS download response was empty or invalid Base64") from None
    max_bytes = int(os.environ.get("INSS_MAX_DOWNLOAD_BYTES") or 50 * 1024 * 1024)
    if not content:
        raise ValueError("INSS download response was empty or invalid Base64")
    if len(content) > max_bytes:
        raise ValueError(f"INSS download exceeds the {max_bytes}-byte download limit")
    name = attachment_filename(requested_name, fallback)
    mime_type = attachment_mime_type(content, name)
    if expected_mime_type and mime_type != expected_mime_type:
        raise ValueError(f"INSS download was not {expected_mime_type}")
    folder = Path(directory or Path.cwd() / "downloaded_files" / "inss")
    folder.mkdir(parents=True, exist_ok=True)
    output = available_path(folder, name)
    with open(output, "xb") as handle:
        handle.write(content)
    pdf = {}
    if mime_type == "application/pdf":
        try:
            pdf = extract_pdf_metadata(content, int(os.environ.get("INSS_MAX_PDF_TEXT_CHARS") or 500_000))
        except Exception as error:
            pdf = {"pdfExtractionError": str(error)}
    return {"path": str(output), "filename": output.name, "bytes": len(content), "mimeType": mime_type, **pdf}


class InssClient:
    """One GERID browser at a time: the A3 profile supports a single Chrome."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._secret = None

    def _gerid_secret(self):
        if os.environ.get("INSS_GERID_TOTP_SECRET"):
            return os.environ["INSS_GERID_TOTP_SECRET"]
        image = Path(os.environ.get("INSS_GERID_QR_IMAGE") or Path.cwd() / "QR_gerid.png").resolve()
        if not image.exists():
            return None
        if self._secret is None:
            self._secret = qr_secret(image, os.environ.get("INSS_GERID_TOTP_ACCOUNT") or "1")
        return self._secret

    async def _using(self, handler):
        async with self._lock:
            browser = await login_and_authorize(
                profile=Path(os.environ.get("INSS_BROWSER_PROFILE") or default_profile()).resolve(),
                profile_directory=os.environ.get("INSS_BROWSER_PROFILE_DIRECTORY"),
                secret=self._gerid_secret(),
                otp=os.environ.get("INSS_GERID_OTP"),
                timeout_ms=int(os.environ.get("INSS_GERID_TIMEOUT_SECONDS") or 180) * 1000,
                headless=True,
                log=_log,
            )
            try:
                await browser.page.goto(TASKS_URL, wait_until="domcontentloaded")
                return await handler(browser.page, browser.summary)
            finally:
                await browser.close()

    @staticmethod
    async def _recaptcha(page):
        await page.wait_for_function("""() => {
            const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
            return typeof api?.execute === "function";
        }""")
        return await page.evaluate(
            """async ({ action, key }) => {
            const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
            return api.execute(key, { action });
        }""",
            {
                "action": os.environ.get("INSS_RECAPTCHA_ACTION") or "buscaRequerimentos",
                "key": os.environ.get("INSS_RECAPTCHA_SITE_KEY") or "6LfA-t8oAAAAAAMYMMHDthsSTek1eOYMwyjJcvoK",
            },
        )

    @staticmethod
    async def _request(page, api_path, method="GET", body=None):
        if not api_path.startswith("/apis/"):
            raise ValueError("INSS API path must start with /apis/")
        result = await page.evaluate(
            f"""async ({{ apiPath, body, method }}) => {{
            const auth = {AUTH_SCRIPT};
            if (!auth) return {{ status: 401, payload: {{ error: "GERID credential missing" }} }};
            const response = await fetch(apiPath, {{
                method, credentials: "include",
                headers: {{ Accept: "application/json", Authorization: `Bearer ${{auth}}`,
                           ...(body === null ? {{}} : {{ "Content-Type": "application/json" }}) }},
                ...(body === null ? {{}} : {{ body: JSON.stringify(body) }}),
            }});
            const text = await response.text();
            let payload;
            try {{ payload = text ? JSON.parse(text) : null; }} catch {{ payload = text; }}
            return {{ status: response.status, payload }};
        }}""",
            {"apiPath": api_path, "body": body, "method": method},
        )
        if not 200 <= result["status"] < 300:
            raise RuntimeError(
                f"INSS {method} {api_path} failed ({result['status']}): "
                f"{json.dumps(result['payload'], ensure_ascii=False)}"
            )
        return result["payload"]

    async def ensure_session(self):
        async def summary(_page, value):
            return value

        return await self._using(summary)

    async def list_notifications(self, first=0):
        async def read(page, _summary):
            return await self._request(
                page,
                f"/apis/requerimentosPortalApi/requerimento/ec/notificacao?first={first}",
                "POST",
                {"tokenReCAPTCHA": await self._recaptcha(page)},
            )

        return await self._using(read)

    async def search_tasks(self, **options):
        async def read(page, _summary):
            query = task_search_request(token=await self._recaptcha(page), **options)
            return await self._request(page, query["path"], "POST", query["body"])

        return await self._using(read)

    async def get_task(self, protocolo):
        _digits(protocolo, "protocolo")
        return await self._using(
            lambda page, _summary: self._request(
                page, f"/apis/requerimentosPortalApi/requerimento/ec/tarefa/{quote(protocolo)}"
            )
        )

    async def download_attachment(self, protocolo, anexo_id, filename=None):
        _digits(protocolo, "protocolo")
        _digits(anexo_id, "anexoId")
        api_path = f"/apis/arquivosPortalApi/tarefa/{quote(protocolo)}/anexo/{quote(anexo_id)}"

        async def download(page, _summary):
            response = await page.evaluate(
                f"""async (requestPath) => {{
                const auth = {AUTH_SCRIPT};
                if (!auth) return {{ status: 401, body: "", disposition: null }};
                const result = await fetch(requestPath, {{ credentials: "include",
                    headers: {{ Accept: "application/json", Authorization: `Bearer ${{auth}}` }} }});
                return {{ status: result.status, body: await result.text(),
                         disposition: result.headers.get("content-disposition") }};
            }}""",
                api_path,
            )
            if not 200 <= response["status"] < 300:
                raise RuntimeError(f"INSS GET {api_path} failed ({response['status']})")
            return save_base64_download(
                response["body"], filename or response["disposition"], f"{protocolo}-{anexo_id}.bin"
            )

        return await self._using(download)

    async def download_process(self, protocolo, filename=None):
        async def download(page, _summary):
            auth = await page.evaluate(f"() => {AUTH_SCRIPT}")
            if not auth:
                raise RuntimeError("GERID credential missing")
            payload = await self._request(page, process_report_request(protocolo, auth))
            file = save_base64_download(
                (payload or {}).get("relatorio"), filename, f"{protocolo}-processo.pdf", "application/pdf"
            )
            return {
                **file,
                "protocolo": payload.get("protocolo", int(protocolo)),
                "permitidoEmissaoSemConfirmacao": payload.get("permitidoEmissaoSemConfirmacao"),
            }

        return await self._using(download)
