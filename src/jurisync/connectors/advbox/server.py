"""ADVBOX MCP server: customers, lawsuits, activities, files, and guarded task writes."""

import base64
import binascii
import datetime
import json
import mimetypes
from typing import Annotated, Any, Literal

import anyio.to_thread
from curl_cffi.requests.exceptions import RequestException
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.connectors.advbox.parsers import compact_lawsuit_report
from jurisync.connectors.advbox.session import (
    VERIFICATION_TRIES,
    AdvboxDeviceVerification,
    CodeRefused,
    pending_verification,
    require_writes,
)
from jurisync.core.documents_dir import resolve_saved_document
from jurisync.core.mcp import READ_ONLY, load_env_file, serve, tool_errors
from jurisync.core.tenant_local import TenantLocal
from jurisync.registry import CONNECTORS

WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
LOCAL_WRITE = WRITE
ERRORS = (ValueError, RuntimeError, OSError, KeyError, PermissionError, RequestException)

Id = Annotated[
    str, Field(pattern=r"^\d+$", description="Numeric ADVBOX ID, sent as a string of digits (e.g. '17519594').")
]
Page = Annotated[int, Field(ge=1, description="Page number, starting at 1.")]
PageSize = Annotated[int, Field(ge=1, le=100, description="Rows per page, 1 to 100.")]
Search = Annotated[str, Field(max_length=255, description="Free text to match; empty string means no filter.")]
OptionalDate = Annotated[
    str,
    Field(
        pattern=r"^(\d{4}-\d{2}-\d{2})?$",
        description="Date as ISO YYYY-MM-DD (e.g. '2026-10-16'); empty string means none.",
    ),
]
Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Date as ISO YYYY-MM-DD (e.g. '2026-10-16').")]
Hour = Annotated[
    str, Field(pattern=r"^(\d{2}:\d{2})?$", description="Time as HH:MM, 24h (e.g. '14:30'); empty string means none.")
]
UserIds = Annotated[
    list[Annotated[int, Field(gt=0)]],
    Field(
        min_length=1,
        max_length=20,
        description="List of 1 to 20 numeric user IDs from advbox_list_members, always a JSON list even for one user (e.g. [961]).",
    ),
]
FilterUserIds = Annotated[
    list[Annotated[int, Field(gt=0)]] | None,
    Field(
        max_length=100,
        description="Optional JSON list of numeric user IDs from advbox_list_members, even for one user (e.g. [961]); omit for everyone.",
    ),
]

# One client (cookie session, CSRF token, rate-limit cooldown) per office; a local install has a single one.
client = TenantLocal(AdvboxClient, close=lambda value: value.close())
# The file tools reach the session through the tenant's current client on every call, so they follow a client that
# was replaced after a credential change instead of keeping the previous account's session.
documents = AdvboxDocuments(client)


class DeviceCode(BaseModel):
    codigo: Annotated[
        str,
        Field(min_length=1, max_length=32, title="Código", description="Código de verificação enviado pelo ADVBOX."),
    ]


def _device_check(error):
    while error is not None and not isinstance(error, AdvboxDeviceVerification):
        error = error.__cause__
    return error


def _can_elicit(context):
    try:
        capabilities = context.session.client_capabilities
    except (AttributeError, ValueError):  # no request behind the context
        return False
    return bool(capabilities and capabilities.elicitation)


async def elicit_device_code(context, sent_to):
    """Ask the user for ADVBOX's device code in the client's own input and send it; the verification result, or None
    when the user declined or the client cannot ask (the agent then uses advbox_verify_device)."""
    if context is None or not _can_elicit(context):
        return None
    session = client.session
    message = (
        f"O ADVBOX pediu a verificação deste dispositivo e enviou um código para {sent_to}. Digite o código recebido."
    )
    for _ in range(VERIFICATION_TRIES):
        try:
            answer = await context.elicit(message, DeviceCode)
        except (MCPError, ValueError):
            return None
        if not isinstance(answer, AcceptedElicitation):
            return None
        with tool_errors("ADVBOX", *ERRORS):
            try:
                return await anyio.to_thread.run_sync(session.verify_device, answer.data.codigo)
            except CodeRefused as refused:
                message = (
                    f"O ADVBOX recusou o código ({refused.left} tentativa(s) restante(s)). "
                    f"Confira o código enviado para {sent_to} e digite de novo."
                )
    return None


class AdvboxServer(MCPServer):
    """When a call stops on ADVBOX's device verification, asks the user for the code (MCP elicitation) and repeats the
    call with the new session. Writes are never repeated: the agent calls them again."""

    async def call_tool(self, name, arguments, context=None):
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as error:
            check = _device_check(error)
            if check is None or name == "advbox_verify_device" or not await elicit_device_code(context, check.sent_to):
                raise
        if name in CONNECTORS["advbox"].write_tools:
            raise ToolError(f"ADVBOX verified this device; {name} was not repeated, call it again")
        return await super().call_tool(name, arguments, context)


server = AdvboxServer("advbox", version="1.0.0")


def advbox_date(value):
    """An ISO ``YYYY-MM-DD`` parameter as ADVBOX takes it (``DD/MM/YYYY``); empty and None pass through."""
    return datetime.date.fromisoformat(value).strftime("%d/%m/%Y") if value else value


def call(handler):
    with tool_errors("ADVBOX", *ERRORS):
        return handler()


def write(handler):
    with tool_errors("ADVBOX", *ERRORS):
        require_writes()
        return handler()


@server.tool(
    name="advbox_ensure_session",
    title="Ensure ADVBOX session",
    description="Authenticate headlessly in ADVBOX using the configured trusted device. Read-only.",
    annotations=READ_ONLY,
)
def ensure_session() -> dict[str, Any]:
    return call(client.ensure_session)


@server.tool(
    name="advbox_verify_device",
    title="Verify ADVBOX device",
    description="Finish ADVBOX's device verification: when a call says ADVBOX sent a code to verify this device, ask the client for it and pass it as code. A refused code can be typed again (3 attempts). Only when the user says the stored device stopped working and ADVBOX did not ask for a code by itself, call it with newDevice=true and no code: it starts a verification with a new device id, ADVBOX sends a code, and the next call passes it. Changes the account's trusted devices; never needs the write permission.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
async def verify_device(
    code: Annotated[
        str,
        Field(max_length=32, description="The code ADVBOX sent, as the client typed it; empty with newDevice=true."),
    ] = "",
    newDevice: Annotated[
        bool, Field(description="true starts a verification with a new device id; only when the user asks for it.")
    ] = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    session = client.session
    with tool_errors("ADVBOX", *ERRORS):
        pending = pending_verification()
        if pending is None and not newDevice:
            return {"verified": False, "message": "Nothing to verify: ADVBOX has not asked to verify this device."}
        if pending is None:
            try:
                return await anyio.to_thread.run_sync(session.start_new_device)
            except AdvboxDeviceVerification as check:
                return await elicit_device_code(ctx, check.sent_to) or {"verified": False, "message": str(check)}
        if not code.strip():
            result = await elicit_device_code(ctx, pending.sent_to)
            if result is None:
                raise ValueError(f"Pass the code ADVBOX sent to {pending.sent_to}")
            return result
        return await anyio.to_thread.run_sync(session.verify_device, code)


@server.tool(
    name="advbox_list_customers",
    title="List ADVBOX customers",
    description="Search and paginate customers in ADVBOX. Read-only.",
    annotations=READ_ONLY,
)
def list_customers(page: Page = 1, pageSize: PageSize = 50, search: Search = "") -> dict[str, Any]:
    return call(lambda: client.list_customers(page=page, pageSize=pageSize, search=search))


@server.tool(
    name="advbox_list_lawsuits",
    title="List ADVBOX lawsuits",
    description="Search and paginate lawsuits in ADVBOX. Supports sorting (created = lawsuits.created_at, movement = date_last_movement — often unpopulated upstream, prefer created) and a created-date range (YYYY-MM-DD). Read-only.",
    annotations=READ_ONLY,
)
def list_lawsuits(
    page: Page = 1,
    pageSize: PageSize = 50,
    search: Search = "",
    sortBy: Annotated[
        Literal["parties", "type", "created", "movement"],
        Field(
            description="Sort column: 'created' (recommended, creation date), 'parties', 'type', or 'movement' (often empty upstream)."
        ),
    ] = "parties",
    sortDir: Annotated[Literal["asc", "desc"], Field(description="'asc' or 'desc'.")] = "asc",
    dateFrom: Annotated[
        OptionalDate,
        Field(description="Created-date range start, YYYY-MM-DD (e.g. '2026-10-01'); empty string for none."),
    ] = "",
    dateUntil: Annotated[
        OptionalDate, Field(description="Created-date range end, YYYY-MM-DD; empty string for none.")
    ] = "",
) -> dict[str, Any]:
    sort = {"parties": 0, "type": 1, "created": 3, "movement": 5}[sortBy]
    return call(
        lambda: client.list_lawsuits(
            page=page,
            pageSize=pageSize,
            search=search,
            sortColumn=sort,
            sortDir=sortDir,
            dateFrom=advbox_date(dateFrom),
            dateUntil=advbox_date(dateUntil),
        )
    )


@server.tool(
    name="advbox_search_tasks",
    title="Search ADVBOX tasks",
    description=(
        "Search tasks (Tarefas) by free text and/or the Tarefas screen filters. search matches the task title, "
        "comment, customer and process number (e.g. a CNJ number, an NB, a client name or an atom like 'ABRIR "
        "INTIMAÇÃO'). Filters: situation (pending or completed), responsibles, start-date range, task type, lawsuit "
        "stage, group, lawsuit type and priority; filter ids come from advbox_task_filters. Without responsible, a "
        "text search covers every member and a listing without text only the logged-in user (allResponsibles "
        "overrides). Completed tasks need dateFrom and dateTo. ADVBOX refuses a filter matching several thousand "
        "tasks: narrow it. Returns compact tasks (postId, task, detail, customer, lawsuitId, taskTypeId, start, "
        "deadline, overdue, responsible; important/urgent/viewed only for your own tasks), total and hasMore. For "
        "a lawsuit's full history use advbox_lawsuit_report. Read-only."
    ),
    annotations=READ_ONLY,
)
def search_tasks(
    search: Search = "",
    completed: Annotated[
        Literal["pendentes", "concluidas"],
        Field(description="'pendentes' (default) or 'concluidas'. Completed tasks need dateFrom/dateTo."),
    ] = "pendentes",
    responsible: FilterUserIds = None,
    allResponsibles: Annotated[
        bool | None,
        Field(
            description="true: every member; false: only the logged-in user. Default: every member when search is given."
        ),
    ] = None,
    dateFrom: Annotated[Date | None, Field(description="Start-date range start YYYY-MM-DD, e.g. '2026-10-15'.")] = None,
    dateTo: Annotated[Date | None, Field(description="Start-date range end YYYY-MM-DD, e.g. '2026-10-15'.")] = None,
    taskType: Annotated[
        str,
        Field(
            pattern=r"^(\d+|publications|schedule)?$",
            description="Task type id from advbox_task_filters taskTypes, or 'publications' / 'schedule'; empty for all.",
        ),
    ] = "",
    stage: Annotated[
        str, Field(pattern=r"^\d*$", description="Lawsuit stage id (Etapa) from advbox_task_filters; empty for all.")
    ] = "",
    groups: Annotated[
        list[Annotated[int, Field(gt=0)]] | None,
        Field(description="JSON list of group ids from advbox_task_filters; omit for all."),
    ] = None,
    lawsuitTypes: Annotated[
        list[Annotated[int, Field(gt=0)]] | None,
        Field(description="JSON list of lawsuit (action) type ids from advbox_task_filters; omit for all."),
    ] = None,
    priorities: Annotated[
        list[Literal["normal", "urgente", "importante"]] | None,
        Field(description="Subset of ['normal', 'urgente', 'importante']; omit for all."),
    ] = None,
    page: Page = 1,
    pageSize: PageSize = 25,
    detailChars: Annotated[
        int, Field(ge=0, le=5000, description="Max characters of each task comment (detail); 0 removes it.")
    ] = 400,
) -> dict[str, Any]:
    return call(
        lambda: client.search_tasks(
            search,
            completed=completed,
            responsible=responsible,
            allResponsibles=allResponsibles,
            dateFrom=advbox_date(dateFrom) or "",
            dateTo=advbox_date(dateTo) or "",
            taskType=taskType,
            stage=stage,
            groups=groups or (),
            lawsuitTypes=lawsuitTypes or (),
            priorities=priorities or (),
            page=page,
            pageSize=pageSize,
            detailChars=detailChars,
        )
    )


@server.tool(
    name="advbox_task_filters",
    title="List ADVBOX task filter options",
    description="Options for advbox_search_tasks filters, read from the Tarefas screen: taskTypes (task templates, plus 'publications' and 'schedule'), stages, groups, lawsuitTypes, situations and priorities, each as {id, label}. Read-only.",
    annotations=READ_ONLY,
)
def task_filters() -> dict[str, Any]:
    return call(client.task_filters)


@server.tool(
    name="advbox_list_publications",
    title="List ADVBOX publications (intimações)",
    description="List the intimações (publications) that ADVBOX received for the office, with the post ID that opens each one, customer, date, court, process number, responsible and status (pendente/concluida). Choose a period (today, 3d, 7d, 30d) or both dateFrom and dateTo, and optionally filter by tribunal sigla (for example TRF5), exact CNJ process number or responsible ID (advbox_list_members). Pages hold at most 200 records (default 50); use page while hasMore is true. The text of one intimação is read with advbox_activity_details using its postId. Fails with a clear message when the account has no Intimações module. Read-only.",
    annotations=READ_ONLY,
)
def list_publications(
    period: Annotated[
        Literal["today", "3d", "7d", "30d"] | None,
        Field(
            description="Shortcut window ending today: 'today', '3d', '7d' or '30d'. Use it alone, or use dateFrom/dateTo instead."
        ),
    ] = None,
    dateFrom: Annotated[Date | None, Field(description="Start date YYYY-MM-DD, e.g. '2026-10-01'.")] = None,
    dateTo: Annotated[Date | None, Field(description="End date YYYY-MM-DD, e.g. '2026-10-15'.")] = None,
    tribunal: Annotated[
        str, Field(max_length=40, description="Court acronym, e.g. 'TRF5' or 'TJPE'. Empty for all courts.")
    ] = "",
    processNumber: Annotated[
        str,
        Field(
            pattern=r"^(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})?$",
            description="Exact CNJ number with punctuation, e.g. '0800963-61.2024.4.05.8308'. Empty for none.",
        ),
    ] = "",
    responsible: Annotated[
        int | None,
        Field(gt=0, description="Optional single numeric user ID from advbox_list_members (a number, not a list)."),
    ] = None,
    page: Page = 1,
    pageSize: Annotated[int, Field(ge=1, le=200, description="Records per page, 1 to 200; default 50.")] = 50,
) -> dict[str, Any]:
    return call(
        lambda: client.list_publications(
            period=period,
            dateFrom=advbox_date(dateFrom) or "",
            dateTo=advbox_date(dateTo) or "",
            tribunal=tribunal,
            processNumber=processNumber,
            responsibleId=responsible or "",
            page=page,
            pageSize=pageSize,
        )
    )


@server.tool(
    name="advbox_list_members",
    title="List ADVBOX members",
    description="List all collaborators (users with IDs, names, usernames) and sector groups (squad ID, name, member IDs) available as task responsibles. Read-only.",
    annotations=READ_ONLY,
)
def list_members() -> dict[str, Any]:
    return call(client.list_members)


@server.tool(
    name="advbox_list_lawsuit_partners",
    title="List ADVBOX lawsuit partners",
    description="List external partners (PARCEIRO) attached to one lawsuit by its ID; appended to the responsible select when creating a task. Empty when the lawsuit has none. Read-only.",
    annotations=READ_ONLY,
)
def list_lawsuit_partners(lawsuitId: Id) -> dict[str, Any]:
    return {"result": call(lambda: client.list_lawsuit_partners(lawsuitId))}


@server.tool(
    name="advbox_list_workflows",
    title="List ADVBOX task templates",
    description="List all workflow templates (task models) with IDs and names, used as the Tarefa template when creating a task. Resolve steps with advbox_workflow_tasks. Read-only.",
    annotations=READ_ONLY,
)
def list_workflows() -> dict[str, Any]:
    return {"result": call(client.list_workflows)}


@server.tool(
    name="advbox_workflow_tasks",
    title="Get ADVBOX workflow steps",
    description="Resolve one workflow template into its ordered steps: template task IDs, default responsible user IDs, and titles. Needed to build the create-task payload. Read-only.",
    annotations=READ_ONLY,
)
def workflow_tasks(workflowId: Id) -> dict[str, Any]:
    return call(lambda: client.workflow_steps(workflowId))


@server.tool(
    name="advbox_activity_details",
    title="Get ADVBOX activity details",
    description="Retrieve full details of one activity (Atividade) by its post ID: status, event date, creator, linked lawsuit CNJ number, parties with phone, action group/type, and lawsuit responsible. Does not mark the activity as viewed. Read-only.",
    annotations=READ_ONLY,
)
def activity_details(postId: Id) -> dict[str, Any]:
    return call(lambda: client.activity_details(postId))


@server.tool(
    name="advbox_lawsuit_report",
    title="Get ADVBOX lawsuit report",
    description="Retrieve the lawsuit report: phase, court (comarca/vara), electronic system, action type, responsible, intimações (court notices with full text), and task/movement history (completed tasks included) with dates and responsibles. Long reports can exceed tool limits: taskSearch keeps only tasks whose text contains it (for example a DJEN ID, to check whether a communication was already handled), maxTasks and maxNotices cap the lists, maxTextChars shortens each text; a compact block reports totals before cutting. Read-only.",
    annotations=READ_ONLY,
)
def lawsuit_report(
    lawsuitId: Id,
    includeMovements: Annotated[bool, Field(description="Include the movement history. Default true.")] = True,
    includePublications: Annotated[
        bool, Field(description="Include the intimações (court notices). Default true.")
    ] = True,
    includeTasks: Annotated[bool, Field(description="Include the task history. Default true.")] = True,
    includeTransactions: Annotated[
        bool, Field(description="Include the financial section (fees, commissions). Default true.")
    ] = True,
    taskSearch: Annotated[
        str,
        Field(max_length=255, description="Keep only tasks whose text contains this, e.g. a DJEN id. Empty for all."),
    ] = "",
    maxTasks: Annotated[
        int, Field(ge=0, le=500, description="Max tasks listed, 0 to 500; 0 means the default cap.")
    ] = 0,
    maxNotices: Annotated[
        int, Field(ge=0, le=100, description="Max intimações listed, 0 to 100; 0 means the default cap.")
    ] = 0,
    maxTextChars: Annotated[
        int, Field(ge=0, le=20000, description="Max characters per text, 0 to 20000; 0 means no cut.")
    ] = 0,
) -> dict[str, Any]:
    return call(
        lambda: compact_lawsuit_report(
            client.lawsuit_report(
                lawsuitId,
                movements=includeMovements,
                publications=includePublications,
                tasks=includeTasks,
                transactions=includeTransactions,
            ),
            task_search=taskSearch,
            max_tasks=maxTasks,
            max_notices=maxNotices,
            max_text=maxTextChars,
        )
    )


@server.tool(
    name="advbox_list_files",
    title="List ADVBOX customer files",
    description="List cloud folders and files attached to a customer (documents are customer-scoped in ADVBOX; lawsuit modals show the customer tree). Returns folder paths with item counts and per-folder zip URLs, and file names with direct download URLs. Pass folder path to list inside a subfolder. Read-only.",
    annotations=READ_ONLY,
)
def list_files(
    customerId: Id,
    folder: Annotated[
        str,
        Field(
            max_length=255, description="Subfolder path inside the customer, as listed earlier; empty lists the root."
        ),
    ] = "",
) -> dict[str, Any]:
    return call(lambda: documents.list_customer_files(customerId, folder))


def _text(value):
    return TextContent(type="text", text=value)


@server.tool(
    name="advbox_download_file",
    title="Download ADVBOX file",
    description="Download one file listed by advbox_list_files using its url/downloadUrl, or a whole folder zip using its zipUrl. Saves a new local file under downloaded_files/advbox without overwriting existing files. PDFs return extracted text to the caller.",
    annotations=LOCAL_WRITE,
)
def download_file(
    url: Annotated[
        str,
        Field(
            min_length=1,
            max_length=2048,
            description="Direct download URL of the file, as returned by advbox_list_files.",
        ),
    ],
    filename: Annotated[
        str | None,
        Field(min_length=1, max_length=255, description="Name to save the file as; default is the name from the URL."),
    ] = None,
) -> CallToolResult:
    metadata = call(lambda: documents.download_file(url, filename=filename))
    markdown = metadata.pop("markdown", None)
    content = [_text(json.dumps(metadata, ensure_ascii=False, indent=2))]
    if markdown:
        content.append(_text(f"Extracted PDF Markdown:\n\n{markdown}"))
    return CallToolResult(content=content, structured_content={**metadata, "textReturned": bool(markdown)})


@server.tool(
    name="advbox_download_folder",
    title="Download and parse ADVBOX folder",
    description="Read up to maxFiles from a customer folder and nested folders using bounded file downloads. Returns per-file metadata and extracted PDF text, with totals and a truncation flag. Read the folder tree first with advbox_list_files.",
    annotations=LOCAL_WRITE,
)
def download_folder(
    customerId: Id,
    folder: Annotated[
        str, Field(min_length=1, max_length=255, description="Folder path to download, as listed by advbox_list_files.")
    ],
    maxFiles: Annotated[int, Field(ge=1, le=100, description="Max files to download, 1 to 100; default 50.")] = 50,
    ocr: Annotated[
        bool, Field(description="Run OCR on scanned PDFs so their text is searchable. Default true; slower.")
    ] = True,
) -> CallToolResult:
    result = call(lambda: documents.download_folder(customerId, folder, max_files=maxFiles, ocr=ocr))
    files = result.pop("files")
    content = [_text(json.dumps(result, ensure_ascii=False, indent=2))]
    content += [_text(f"--- {file['name']} ---\n\n{file['markdown']}") for file in files if file.get("markdown")]
    return CallToolResult(
        content=content, structured_content={**result, "filesParsed": sum(1 for file in files if file.get("markdown"))}
    )


@server.tool(
    name="advbox_save_folder",
    title="Save an ADVBOX folder into the shared documents folder",
    description="Save the files of a customer folder (and nested folders), unparsed, into a named subfolder of the shared documents folder, for a workflow that needs the whole set, such as filing_prepare. Never overwrites: a repeated name is suffixed. A name the shared folder does not accept (other symbols, a leading dot or underscore, over 181 characters) is adjusted and the entry reports renamedFrom with the ADVBOX name. Read the folder tree first with advbox_list_files.",
    annotations=LOCAL_WRITE,
)
def save_folder(
    customerId: Id,
    folder: Annotated[
        str, Field(min_length=1, max_length=255, description="Folder path to save, as listed by advbox_list_files.")
    ],
    into: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
            description="Name of the shared-documents subfolder to save into, e.g. the filing runId.",
        ),
    ],
    maxFiles: Annotated[int, Field(ge=1, le=100, description="Max files to save, 1 to 100; default 50.")] = 50,
) -> dict[str, Any]:
    return call(lambda: documents.save_folder(customerId, folder, into, max_files=maxFiles))


MAX_UPLOAD_FILES = 10


class Attachment(BaseModel):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    contentBase64: Annotated[str, Field(min_length=1)]
    contentType: Annotated[str, Field(max_length=127)] = "application/pdf"


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ATTACHMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".txt"}
# A customer folder also keeps office documents (drafts, spreadsheets).
UPLOAD_SUFFIXES = ATTACHMENT_SUFFIXES | {".doc", ".docx", ".odt", ".xls", ".xlsx"}


def _saved_files(names, suffixes=ATTACHMENT_SUFFIXES):
    """Read files that another tool saved in the shared documents folder (confined to that folder)."""
    files = []
    for name in names:
        path = resolve_saved_document(name)
        if path.suffix.lower() not in suffixes:
            raise ValueError(f"Attachment {name} is not a supported type ({', '.join(sorted(suffixes))})")
        data = path.read_bytes()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Attachment {name} is larger than {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
        files.append(
            {
                "filename": path.name,
                "bytes": data,
                "contentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            }
        )
    return files


FolderPath = Annotated[
    str,
    Field(
        max_length=255,
        pattern=r"^[^\\\x00-\x1f]*$",
        description="Customer folder path exactly as advbox_list_files lists it (e.g. '00-PROTOCOLAR' or "
        "'00-PROTOCOLAR/Anexos'); empty string means the customer's root.",
    ),
]


@server.tool(
    name="advbox_create_folder",
    title="Create ADVBOX customer folder",
    description="Create a folder in a customer's files (documents are customer-scoped in ADVBOX: get the customerId from advbox_list_customers or the lawsuit). ADVBOX folders are flat: there are no subfolders. Idempotent: when a folder of the same name (ignoring case) already exists it is returned with existing=true and nothing is created, so a retry never duplicates it. Names: one level, 1 to 40 characters, no slash. With dryRun=true nothing is sent: the existing folder, or the request that would be sent, is returned. If ADVBOX does not confirm the creation the folder is looked up again; when still unknown the error says it MAY have been created. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
def create_folder(
    customerId: Id,
    name: Annotated[
        str, Field(min_length=1, max_length=40, description="New folder name, one level, e.g. 'Documentos pessoais'.")
    ],
    dryRun: Annotated[
        bool, Field(description="true checks for an existing folder and sends nothing. Use it first.")
    ] = False,
) -> dict[str, Any]:
    def run():
        return documents.create_folder(customerId, name, dry_run=dryRun)

    return call(run) if dryRun else write(run)


@server.tool(
    name="advbox_upload_files",
    title="Upload files into an ADVBOX customer folder",
    description="Upload 1 to 10 files from the shared documents folder (the savedAs names returned by pje_read_documents with save=true, advbox_download_file, etc.) into an existing customer folder (create it first with advbox_create_folder). Each file is stored by ADVBOX under its own name (<name>_<number>.<ext>); the result lists, per file, the storedName, fileId, url and whether the folder listing shows it. All-or-nothing on a clear refusal: files already uploaded by the call are deleted again and the error says so. If ADVBOX does not answer (timeout, connection error, 5xx) the error says the file MAY have been uploaded and which ones were confirmed or not sent: list the folder with advbox_list_files before retrying and send only what is missing; the call is never replayed. Not idempotent: repeating it uploads copies. With dryRun=true the folder and files are checked and nothing is sent. Types: pdf, png, jpg, jpeg, mp3, txt, doc, docx, odt, xls, xlsx; at most 25 MB each. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=WRITE,
)
def upload_files(
    customerId: Id,
    folder: Annotated[
        FolderPath,
        Field(min_length=1, description="Target folder path as listed by advbox_list_files, e.g. '00-PROTOCOLAR'."),
    ],
    files: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(
            min_length=1,
            max_length=MAX_UPLOAD_FILES,
            description='JSON list of file names in the shared documents folder, e.g. ["procuracao.pdf"]. Plain names only, no paths.',
        ),
    ],
    dryRun: Annotated[
        bool, Field(description="true checks the folder and reads the files without uploading. Use it first.")
    ] = False,
) -> dict[str, Any]:
    def run():
        loaded = _saved_files(files, UPLOAD_SUFFIXES)
        return documents.upload_files(customerId, folder, loaded, dry_run=dryRun)

    return call(run) if dryRun else write(run)


@server.tool(
    name="advbox_create_task",
    title="Create ADVBOX task",
    description="Create a task on a lawsuit with a task-type ID (tasksId), responsible guest IDs (guests: a JSON list of 1 or more user IDs from advbox_list_members; a task cannot be created with no responsible), a date in YYYY-MM-DD, and comments. Attachments can be given inline as base64 (attachments) or by file name from the shared documents folder (attachmentFiles, the savedAs names returned by pje_read_documents with save=true); at most 5 files in total. With dryRun=true nothing is sent: the task body and files are validated and returned. dedupeKey (e.g. the DJEN communication id) makes a retry safe: a pending task of this lawsuit already marked [ref:<dedupeKey>] is returned with duplicate=true and nothing is created; otherwise the marker becomes the last line of the comment. If ADVBOX does not confirm the creation (timeout, connection error, 5xx) the error says the task MAY have been created: search the lawsuit's tasks before retrying, the call is never replayed. Creating requires ADVBOX_ALLOW_WRITES=true; attachments also need ADVBOX_UPLOAD_FOLDER.",
    annotations=WRITE,
)
def create_task(
    lawsuitId: Id,
    tasksId: Annotated[
        str,
        Field(
            pattern=r"^\d+$",
            description="Task template ID (the task type), from advbox_workflow_tasks steps or advbox_list_workflows.",
        ),
    ],
    guests: UserIds,
    date: Date,
    workflowId: Annotated[
        str,
        Field(
            pattern=r"^\d*$",
            description="Workflow template id from advbox_list_workflows; empty to create one task without a workflow.",
        ),
    ] = "",
    workflowSequence: Annotated[
        str, Field(pattern=r"^\d*$", description="Position of this task in the workflow, starting at '1'.")
    ] = "1",
    workflowLimit: Annotated[
        str, Field(pattern=r"^\d*$", description="Max number of workflow steps to create; empty for all.")
    ] = "",
    hour: Hour = "",
    deadline: OptionalDate = "",
    comments: Annotated[str, Field(max_length=2000, description="Task note, up to 2000 characters. Plain text.")] = "",
    attachments: Annotated[
        list[Attachment],
        Field(
            max_length=MAX_ATTACHMENTS,
            description="Inline files as base64 objects (filename, contentBase64, contentType). Count together with attachmentFiles, max 5 total.",
        ),
    ] = [],
    attachmentFiles: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(
            max_length=5,
            description='JSON list of file names saved by pje_read_documents (save=true) or advbox_download_file, e.g. ["ata.pdf"]. Max 5 total with attachments.',
        ),
    ] = [],
    dryRun: Annotated[
        bool, Field(description="true validates and returns the body without sending. Use it first.")
    ] = False,
    dedupeKey: Annotated[
        str,
        Field(
            max_length=80,
            pattern=r"^[^\[\]\r\n]*$",
            description="Optional reference that identifies what this task answers, e.g. the DJEN communication id '747498033'. One line, no square brackets. When given, an existing pending task of the lawsuit marked [ref:<dedupeKey>] is returned (duplicate=true) instead of creating a second one.",
        ),
    ] = "",
) -> dict[str, Any]:
    def run():
        if len(attachments) + len(attachmentFiles) > MAX_ATTACHMENTS:
            raise ValueError(f"At most {MAX_ATTACHMENTS} attachments per task")
        try:
            files = [
                {
                    "filename": item.filename,
                    "bytes": base64.b64decode(item.contentBase64, validate=True),
                    "contentType": item.contentType,
                }
                for item in attachments
            ]
        except binascii.Error:
            raise ToolError("ADVBOX request failed: attachment content is not valid base64") from None
        files += _saved_files(attachmentFiles)
        options = {
            "lawsuitId": lawsuitId,
            "tasksId": tasksId,
            "guests": guests,
            "workflowId": workflowId,
            "workflowSequence": workflowSequence,
            "workflowLimit": workflowLimit,
            "date": advbox_date(date),
            "hour": hour,
            "deadline": advbox_date(deadline),
            "comments": comments,
        }
        if dryRun:
            return client.preview_task(options, files, dedupe_key=dedupeKey)
        return client.create_task(options, files, dedupe_key=dedupeKey)

    return call(run) if dryRun else write(run)


@server.tool(
    name="advbox_update_task",
    title="Update ADVBOX task",
    description="Edit a PENDING task the way the ADVBOX screen does: change its comment (comments replaces, appendComments adds a paragraph), date, hour, deadline or guests, and add or remove attachments. Fields left out stay as they are; hour or deadline set to an empty string clear them. IMPORTANT: ADVBOX replaces the task, so the result has a NEW postId (replacedPostId is the old one, which no longer exists). Existing attachments are kept; new files come from attachmentFiles (names saved by pje_read_documents) or base64 attachments; removeFiles names attachments to delete. Use expectComments (a text the current comment must contain) so the edit is refused if the post ID points at a different task. With dryRun=true nothing is sent and the body that would be sent is returned. Once the replacement exists the call does not fail: a file that could not be removed is listed in removeErrors and warnings, next to the new postId. If ADVBOX does not confirm the edit (timeout, connection error, 5xx) the error says the task MAY have been replaced: search the lawsuit's tasks before retrying. Do not edit completed tasks. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=DESTRUCTIVE,
)
def update_task(
    postId: Id,
    comments: Annotated[
        str | None, Field(max_length=20000, description="Replaces the whole comment. Leave empty to keep it.")
    ] = None,
    appendComments: Annotated[
        str | None,
        Field(max_length=5000, description="Adds a paragraph to the end of the comment without replacing it."),
    ] = None,
    date: Annotated[
        Date | None, Field(description="New date YYYY-MM-DD, e.g. '2026-10-20'. Leave empty to keep it.")
    ] = None,
    hour: Annotated[
        Hour | None, Field(description="New time HH:MM, 24h. Empty string clears it; leave out to keep it.")
    ] = None,
    deadline: Annotated[
        OptionalDate | None, Field(description="New deadline YYYY-MM-DD. Empty string clears it; leave out to keep it.")
    ] = None,
    guests: Annotated[
        UserIds | None,
        Field(
            description="New responsible user ids, JSON list from advbox_list_members, e.g. [961]. Leave out to keep them."
        ),
    ] = None,
    attachments: Annotated[
        list[Attachment],
        Field(
            max_length=MAX_ATTACHMENTS,
            description="New inline files as base64 objects. Count with attachmentFiles, max 5 total.",
        ),
    ] = [],
    attachmentFiles: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(max_length=5, description='JSON list of saved file names to attach, e.g. ["ata.pdf"]. Max 5 total.'),
    ] = [],
    removeFiles: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=255)]],
        Field(max_length=10, description="JSON list of attachment names to delete from the task, max 10."),
    ] = [],
    expectComments: Annotated[
        str,
        Field(
            max_length=255,
            description="Text the current comment must contain; the edit is refused otherwise. Guards against editing the wrong task.",
        ),
    ] = "",
    dryRun: Annotated[
        bool, Field(description="true returns the body that would be sent, without sending. Use it first.")
    ] = False,
) -> dict[str, Any]:
    def run():
        if len(attachments) + len(attachmentFiles) > MAX_ATTACHMENTS:
            raise ValueError(f"At most {MAX_ATTACHMENTS} attachments per edit")
        try:
            files = [
                {
                    "filename": item.filename,
                    "bytes": base64.b64decode(item.contentBase64, validate=True),
                    "contentType": item.contentType,
                }
                for item in attachments
            ]
        except binascii.Error:
            raise ToolError("ADVBOX request failed: attachment content is not valid base64") from None
        files += _saved_files(attachmentFiles)
        changes = {
            "comments": comments,
            "append_comments": appendComments,
            "date": advbox_date(date),
            "hour": hour,
            "deadline": advbox_date(deadline),
            "guests": guests,
        }
        if dryRun:
            return client.preview_update(
                postId, changes, files, expect_comments=expectComments, remove_files=removeFiles
            )
        return client.update_task(postId, changes, files, expect_comments=expectComments, remove_files=removeFiles)

    return call(run) if dryRun else write(run)


ExpectComments = Annotated[
    str,
    Field(
        max_length=255,
        description="Text the task's current comment must contain; the call is refused otherwise. Guards against acting on the wrong task.",
    ),
]
DryRun = Annotated[
    bool, Field(description="true changes nothing and returns the task that would be affected. Use it first.")
]


@server.tool(
    name="advbox_complete_task",
    title="Complete ADVBOX task",
    description="Mark a PENDING task completed by its post ID. With dryRun=true nothing is sent and the task that would be completed is returned (type, guests, dates, comment, files). expectComments refuses the call when the task's comment does not contain that text. The result is confirmed (the task left the pending list); when ADVBOX does not confirm it the call fails instead of reporting success. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=DESTRUCTIVE,
)
def complete_task(postId: Id, expectComments: ExpectComments = "", dryRun: DryRun = False) -> dict[str, Any]:
    def run():
        return client.complete_task(postId, expect_comments=expectComments, dry_run=dryRun)

    return call(run) if dryRun else write(run)


@server.tool(
    name="advbox_delete_task",
    title="Delete ADVBOX task",
    description="Soft-delete a task by its post ID (scope this/future/all for recurring tasks). The task leaves listings; attachments survive. With dryRun=true nothing is sent and the task that would be deleted is returned. expectComments refuses the call when the task's comment does not contain that text. The deletion is confirmed from ADVBOX's answer; without that confirmation the call fails instead of returning deleted=false. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=DESTRUCTIVE,
)
def delete_task(
    postId: Id,
    scope: Annotated[
        Literal["this", "future", "all"],
        Field(
            description="Which tasks to delete: 'this' (default, only this one), 'future' or 'all' (recurring copies). Destructive: confirm with the user first."
        ),
    ] = "this",
    comments: Annotated[
        str, Field(max_length=2000, description="Optional note recorded with the deletion, up to 2000 characters.")
    ] = "",
    expectComments: ExpectComments = "",
    dryRun: DryRun = False,
) -> dict[str, Any]:
    def run():
        return client.delete_task(
            postId, scope=scope, comments=comments, expect_comments=expectComments, dry_run=dryRun
        )

    return call(run) if dryRun else write(run)


def main():
    load_env_file()
    serve(server, on_exit=client.close)


if __name__ == "__main__":
    main()
