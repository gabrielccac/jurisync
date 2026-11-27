"""ADVBOX MCP server: customers, lawsuits, activities, files, and guarded task writes."""

import base64
import binascii
import datetime
import json
import mimetypes
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent
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
from jurisync.core import calls, names
from jurisync.core.documents_dir import resolve_saved_document
from jurisync.core.errors import InputError
from jurisync.core.mcp import (
    DESTRUCTIVE,
    READ_ONLY,
    REPLACE,
    WRITE,
    WRITE_IDEMPOTENT,
    JurisyncServer,
    serve,
    tool_error,
    tool_errors,
)
from jurisync.core.tenant_local import TenantLocal
from jurisync.customers import load_env_file
from jurisync.registry import CONNECTORS

DESTRUCTIVE = DESTRUCTIVE

Id = Annotated[str, Field(pattern=r"^\d+$")]
Page = Annotated[int, Field(ge=1)]
PageSize = Annotated[int, Field(ge=1, le=100)]
Search = Annotated[str, Field(max_length=255, description="Empty = no filter.")]
OptionalDate = Annotated[
    str,
    Field(
        pattern=r"^(\d{4}-\d{2}-\d{2})?$",
    ),
]
Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Hour = Annotated[str, Field(pattern=r"^(\d{2}:\d{2})?$")]
UserIds = Annotated[
    list[Annotated[int, Field(gt=0)]],
    Field(
        min_length=1,
        max_length=20,
    ),
]
FilterUserIds = Annotated[
    list[Annotated[int, Field(gt=0)]] | None,
    Field(
        max_length=100,
        description="User IDs (advbox_list_members), JSON list; omit for everyone.",
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
        with tool_errors("ADVBOX"):
            try:
                return await anyio.to_thread.run_sync(session.verify_device, answer.data.codigo)
            except CodeRefused as refused:
                message = (
                    f"O ADVBOX recusou o código ({refused.left} tentativa(s) restante(s)). "
                    f"Confira o código enviado para {sent_to} e digite de novo."
                )
    return None


class AdvboxServer(JurisyncServer):
    """When a call stops on ADVBOX's device verification, asks the user for the code (MCP elicitation) and repeats the
    call with the new session. Writes are never repeated: the agent calls them again."""

    async def call_tool(self, name, arguments, context=None):
        name = names.canonical(name)
        with calls.bound():
            return await self._call_with_verification(name, arguments, context)

    async def _call_with_verification(self, name, arguments, context):
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as error:
            check = _device_check(error)
            if check is None or name == "advbox_verify_device" or not await elicit_device_code(context, check.sent_to):
                raise
        if name in CONNECTORS["advbox"].write_tools:
            raise tool_error(
                "ADVBOX",
                InputError(
                    f"ADVBOX verified this device; {name} was not repeated",
                    code="device_verified",
                    retryable=True,
                    next_step="chame a mesma escrita de novo; a verificação já foi feita.",
                ),
            )
        return await super().call_tool(name, arguments, context)


def advbox_date(value):
    """An ISO ``YYYY-MM-DD`` parameter as ADVBOX takes it (``DD/MM/YYYY``); empty and None pass through."""
    return datetime.date.fromisoformat(value).strftime("%d/%m/%Y") if value else value


def call(handler):
    with tool_errors("ADVBOX"):
        return handler()


def write(handler):
    with tool_errors("ADVBOX"):
        require_writes()
        return handler()


def _text(value):
    return TextContent(type="text", text=value)


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
        description="Customer folder path as advbox_list_files lists it; empty = root.",
    ),
]


StageId = Annotated[
    str,
    Field(
        pattern=r"^\d+$",
        description="Stage id (Fase) from advbox_list_task_filters stages, e.g. '3'.",
    ),
]
SubStageId = Annotated[
    str,
    Field(
        pattern=r"^\d*$",
        description="Sub-stage (Etapa) from a dryRun's subStageOptions. Empty: first of a new stage, else current.",
    ),
]


ExpectComments = Annotated[
    str,
    Field(
        max_length=255,
        description="Text the current comment must contain, else refused.",
    ),
]
DryRun = Annotated[bool, Field(description="Change nothing; return the task. Use first.")]


def create_server():
    server = AdvboxServer("advbox")

    @server.tool(
        name="advbox_get_session",
        title="Ensure ADVBOX session",
        description="Authenticate headlessly with the configured trusted device. Read-only.",
        annotations=READ_ONLY,
    )
    def ensure_session() -> dict[str, Any]:
        return call(client.ensure_session)

    @server.tool(
        name="advbox_verify_device",
        title="Verify ADVBOX device",
        description="When a call says ADVBOX sent a device code, ask the user and pass it (3 attempts). newDevice=true only if a call says the account has no verified device, or the user says the stored one stopped working, and the user agrees to receive a code; then pass it. Changes the account's trusted devices; no write grant needed.",
        annotations=WRITE,
    )
    async def verify_device(
        code: Annotated[
            str,
            Field(max_length=32, description="Code ADVBOX sent; empty with newDevice=true."),
        ] = "",
        newDevice: Annotated[bool, Field(description="Only with the user's agreement: ADVBOX e-mails a code.")] = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = client.session
        with tool_errors("ADVBOX"):
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
        description="Search and page customers. Read-only.",
        annotations=READ_ONLY,
    )
    def list_customers(page: Page = 1, pageSize: PageSize = 50, search: Search = "") -> dict[str, Any]:
        return call(lambda: client.list_customers(page=page, pageSize=pageSize, search=search))

    @server.tool(
        name="advbox_list_lawsuits",
        title="List ADVBOX lawsuits",
        description="Search and page lawsuits. Read-only.",
        annotations=READ_ONLY,
    )
    def list_lawsuits(
        page: Page = 1,
        pageSize: PageSize = 50,
        search: Search = "",
        sortBy: Annotated[
            Literal["parties", "type", "created", "movement"],
            Field(description="'created' recommended; 'movement' is often empty."),
        ] = "parties",
        sortDir: Literal["asc", "desc"] = "asc",
        dateFrom: Annotated[
            OptionalDate,
            Field(description="Created from; empty = none."),
        ] = "",
        dateUntil: Annotated[OptionalDate, Field(description="Created until; empty = none.")] = "",
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
        description="Search tasks (Tarefas) by text and/or filters (ids from advbox_list_task_filters). search matches title, comment, customer, process number (CNJ, NB, atoms like 'ABRIR INTIMAÇÃO'). Without responsible, a text search covers every member, a listing without text only you. Completed tasks need dateFrom and dateTo. Filters matching thousands of tasks are refused. Returns compact tasks (important/urgent/viewed only for your own), total and hasMore. Read-only.",
        annotations=READ_ONLY,
    )
    def search_tasks(
        search: Search = "",
        completed: Annotated[
            Literal["pendentes", "concluidas"],
            Field(description="'concluidas' needs dateFrom/dateTo."),
        ] = "pendentes",
        responsible: FilterUserIds = None,
        allResponsibles: Annotated[
            bool | None,
            Field(description="true: every member; false: only you."),
        ] = None,
        dateFrom: Annotated[Date | None, Field(description="Task start from.")] = None,
        dateTo: Annotated[Date | None, Field(description="Task start until.")] = None,
        taskType: Annotated[
            str,
            Field(
                pattern=r"^(\d+|publications|schedule)?$",
                description="taskTypes id; empty = all.",
            ),
        ] = "",
        stage: Annotated[str, Field(pattern=r"^\d*$", description="Empty = all.")] = "",
        groups: list[Annotated[int, Field(gt=0)]] | None = None,
        lawsuitTypes: list[Annotated[int, Field(gt=0)]] | None = None,
        priorities: list[Literal["normal", "urgente", "importante"]] | None = None,
        page: Page = 1,
        pageSize: PageSize = 25,
        detailChars: Annotated[int, Field(ge=0, le=5000, description="Max chars of each detail; 0 removes it.")] = 400,
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
        name="advbox_list_task_filters",
        title="List ADVBOX task filter options",
        description="Filter options ({id, label}) for advbox_search_tasks: taskTypes, stages, groups, lawsuitTypes, situations, priorities. Read-only.",
        annotations=READ_ONLY,
    )
    def task_filters() -> dict[str, Any]:
        return call(client.task_filters)

    @server.tool(
        name="advbox_list_publicacoes",
        title="List ADVBOX publications (intimações)",
        description="Intimações received by the office, with postId and status. Give period or dateFrom+dateTo; page while hasMore. Text: advbox_get_activity(postId). Fails if the account lacks the Intimações module. Read-only.",
        annotations=READ_ONLY,
    )
    def list_publications(
        period: Annotated[
            Literal["today", "3d", "7d", "30d"] | None,
            Field(description="Window ending today; or use dateFrom/dateTo."),
        ] = None,
        dateFrom: Date | None = None,
        dateTo: Date | None = None,
        tribunal: Annotated[str, Field(max_length=40, description="Court acronym, e.g. 'TRF5'; empty = all.")] = "",
        processNumber: Annotated[
            str,
            Field(
                pattern=r"^(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})?$",
                description="Exact CNJ with punctuation.",
            ),
        ] = "",
        responsible: Annotated[
            int | None,
            Field(gt=0, description="One user ID from advbox_list_members (a number, not a list)."),
        ] = None,
        page: Page = 1,
        pageSize: Annotated[int, Field(ge=1, le=200)] = 50,
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
        description="Collaborators and sector groups (with member ids) usable as task responsibles. Read-only.",
        annotations=READ_ONLY,
    )
    def list_members() -> dict[str, Any]:
        return call(client.list_members)

    @server.tool(
        name="advbox_list_lawsuit_partners",
        title="List ADVBOX lawsuit partners",
        description="External partners (PARCEIRO) of one lawsuit, also selectable as task responsibles. Read-only.",
        annotations=READ_ONLY,
    )
    def list_lawsuit_partners(lawsuitId: Id) -> dict[str, Any]:
        return {"result": call(lambda: client.list_lawsuit_partners(lawsuitId))}

    @server.tool(
        name="advbox_list_workflows",
        title="List ADVBOX task templates",
        description="Workflow templates (task models); steps via advbox_list_workflow_tasks. Read-only.",
        annotations=READ_ONLY,
    )
    def list_workflows() -> dict[str, Any]:
        return {"result": call(client.list_workflows)}

    @server.tool(
        name="advbox_list_workflow_tasks",
        title="Get ADVBOX workflow steps",
        description="Ordered steps of a workflow template (task ids, default responsibles) for advbox_create_task. Read-only.",
        annotations=READ_ONLY,
    )
    def workflow_tasks(workflowId: Id) -> dict[str, Any]:
        return call(lambda: client.workflow_steps(workflowId))

    @server.tool(
        name="advbox_get_activity",
        title="Get ADVBOX activity details",
        description="Details of one activity (Atividade) by postId. Does not mark it viewed. Read-only.",
        annotations=READ_ONLY,
    )
    def activity_details(postId: Id) -> dict[str, Any]:
        return call(lambda: client.activity_details(postId))

    @server.tool(
        name="advbox_get_lawsuit_report",
        title="Get ADVBOX lawsuit report",
        description="Lawsuit report: phase, court, action type, responsible, intimações with full text, task/movement history. To fit output limits use taskSearch (e.g. a DJEN id, to check whether it was handled), maxTasks, maxNotices, maxTextChars; the compact block reports totals before cutting. Read-only.",
        annotations=READ_ONLY,
    )
    def lawsuit_report(
        lawsuitId: Id,
        includeMovements: bool = True,
        includePublications: bool = True,
        includeTasks: bool = True,
        includeTransactions: bool = True,
        taskSearch: Annotated[
            str,
            Field(max_length=255, description="Keep only tasks containing this text."),
        ] = "",
        maxTasks: Annotated[int, Field(ge=0, le=500, description="0 = default cap.")] = 0,
        maxNotices: Annotated[int, Field(ge=0, le=100, description="0 = default cap.")] = 0,
        maxTextChars: Annotated[int, Field(ge=0, le=20000, description="Max chars per text; 0 = no cut.")] = 0,
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
        description="Folders (with zipUrl) and files (with url) of a customer; files are customer-scoped. Read-only.",
        annotations=READ_ONLY,
    )
    def list_files(
        customerId: Id,
        folder: Annotated[
            str,
            Field(max_length=255, description="Subfolder path as listed; empty = root."),
        ] = "",
    ) -> dict[str, Any]:
        return call(lambda: documents.list_customer_files(customerId, folder))

    @server.tool(
        name="advbox_download_file",
        title="Download ADVBOX file",
        description="Save a file (url) or folder zip (zipUrl) from advbox_list_files as a new local file (never overwrites); PDFs return text.",
        annotations=WRITE,
    )
    def download_file(
        url: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2048,
                description="URL from advbox_list_files.",
            ),
        ],
        filename: Annotated[
            str | None,
            Field(min_length=1, max_length=255, description="Save-as name; default from the URL."),
        ] = None,
    ) -> CallToolResult:
        metadata = call(lambda: documents.download_file(url, filename=filename))
        markdown = metadata.pop("markdown", None)
        structured = {**metadata, "textReturned": bool(markdown)}
        content = [_text(json.dumps(structured, ensure_ascii=False, indent=2))]
        if markdown:
            content.append(_text(f"Extracted PDF Markdown:\n\n{markdown}"))
        return CallToolResult(content=content, structured_content=structured)

    @server.tool(
        name="advbox_download_folder",
        title="Download and parse ADVBOX folder",
        description="Download and parse up to maxFiles of a customer folder and subfolders: PDF text per file, totals, truncation flag.",
        annotations=WRITE,
    )
    def download_folder(
        customerId: Id,
        folder: Annotated[str, Field(min_length=1, max_length=255, description="Folder path from advbox_list_files.")],
        maxFiles: Annotated[int, Field(ge=1, le=100)] = 50,
        ocr: Annotated[bool, Field(description="OCR scanned PDFs (slower).")] = True,
    ) -> CallToolResult:
        result = call(lambda: documents.download_folder(customerId, folder, max_files=maxFiles, ocr=ocr))
        files = result.pop("files")
        structured = {**result, "filesParsed": sum(1 for file in files if file.get("markdown"))}
        content = [_text(json.dumps(structured, ensure_ascii=False, indent=2))]
        content += [_text(f"--- {file['name']} ---\n\n{file['markdown']}") for file in files if file.get("markdown")]
        return CallToolResult(content=content, structured_content=structured)

    @server.tool(
        name="advbox_save_folder",
        title="Save an ADVBOX folder into the shared documents folder",
        description="Copy a customer folder (with subfolders), unparsed, into a shared-documents subfolder (e.g. for filing_prepare). Never overwrites (repeated names get a suffix); refused names are adjusted and reported with renamedFrom.",
        annotations=WRITE,
    )
    def save_folder(
        customerId: Id,
        folder: Annotated[str, Field(min_length=1, max_length=255, description="Folder path from advbox_list_files.")],
        into: Annotated[
            str,
            Field(
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                description="Shared-documents subfolder, e.g. the filing runId.",
            ),
        ],
        maxFiles: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return call(lambda: documents.save_folder(customerId, folder, into, max_files=maxFiles))

    @server.tool(
        name="advbox_create_folder",
        title="Create ADVBOX customer folder",
        description="Create a flat (one level) folder in a customer's files. Idempotent: an existing same-name folder is returned with existing=true. If creation is unconfirmed the error says it MAY exist. Needs ADVBOX_ALLOW_WRITES.",
        annotations=WRITE_IDEMPOTENT,
    )
    def create_folder(
        customerId: Id,
        name: Annotated[str, Field(min_length=1, max_length=40, description="One level, e.g. 'Documentos pessoais'.")],
        dryRun: Annotated[bool, Field(description="Send nothing. Use first.")] = False,
    ) -> dict[str, Any]:
        def run():
            return documents.create_folder(customerId, name, dry_run=dryRun)

        return call(run) if dryRun else write(run)

    @server.tool(
        name="advbox_upload_files",
        title="Upload files into an ADVBOX customer folder",
        description="Upload files from the shared documents folder (savedAs names from court tools or advbox_download_file) into an existing customer folder. Stored as <name>_<number>.<ext> (storedName). On a clear refusal, files already uploaded are deleted again. If ADVBOX does not answer, the error says which files MAY be uploaded: list the folder and send only what is missing. Not idempotent: repeating uploads copies. Types: pdf, png, jpg, jpeg, mp3, txt, doc, docx, odt, xls, xlsx; max 25 MB each. Needs ADVBOX_ALLOW_WRITES.",
        annotations=WRITE,
    )
    def upload_files(
        customerId: Id,
        folder: Annotated[
            FolderPath,
            Field(min_length=1, description="Folder path from advbox_list_files, e.g. '00-PROTOCOLAR'."),
        ],
        files: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=128)]],
            Field(
                min_length=1,
                max_length=MAX_UPLOAD_FILES,
                description='Shared-documents file names, no paths, e.g. ["procuracao.pdf"].',
            ),
        ],
        dryRun: Annotated[bool, Field(description="Check without uploading. Use first.")] = False,
    ) -> dict[str, Any]:
        def run():
            loaded = _saved_files(files, UPLOAD_SUFFIXES)
            return documents.upload_files(customerId, folder, loaded, dry_run=dryRun)

        return call(run) if dryRun else write(run)

    @server.tool(
        name="advbox_create_task",
        title="Create ADVBOX task",
        description="Create a task on a lawsuit. Attachments: base64 or savedAs names (attachmentFiles), max 5 total. dedupeKey: a pending task marked [ref:<dedupeKey>] is returned (duplicate=true) instead of a new one. If creation is unconfirmed the error says it MAY exist: search the lawsuit's tasks before retrying. stageId then moves the lawsuit; if that fails, stage.error is set: use advbox_update_lawsuit_stage, never recreate the task. Needs ADVBOX_ALLOW_WRITES; attachments also ADVBOX_UPLOAD_FOLDER.",
        annotations=WRITE,
    )
    def create_task(
        lawsuitId: Id,
        tasksId: Annotated[
            str,
            Field(
                pattern=r"^\d+$",
                description="Task type id (advbox_list_workflow_tasks).",
            ),
        ],
        guests: Annotated[
            UserIds, Field(description="User IDs from advbox_list_members, always a JSON list, e.g. [961].")
        ],
        date: Date,
        workflowId: Annotated[
            str,
            Field(
                pattern=r"^\d*$",
                description="Workflow template id; empty for a single task.",
            ),
        ] = "",
        workflowSequence: Annotated[str, Field(pattern=r"^\d*$", description="Position in the workflow.")] = "1",
        workflowLimit: Annotated[
            str, Field(pattern=r"^\d*$", description="Max workflow steps to create; empty = all.")
        ] = "",
        hour: Annotated[Hour, Field(description="HH:MM 24h.")] = "",
        deadline: Annotated[OptionalDate, Field(description="YYYY-MM-DD.")] = "",
        comments: Annotated[str, Field(max_length=2000, description="Plain-text task note.")] = "",
        attachments: Annotated[
            list[Attachment],
            Field(
                max_length=MAX_ATTACHMENTS,
                description="Inline base64 files; max 5 with attachmentFiles.",
            ),
        ] = [],
        attachmentFiles: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=128)]],
            Field(
                max_length=5,
                description='savedAs names in the shared documents folder, e.g. ["ata.pdf"].',
            ),
        ] = [],
        dryRun: Annotated[bool, Field(description="Return the body without sending. Use first.")] = False,
        dedupeKey: Annotated[
            str,
            Field(
                max_length=80,
                pattern=r"^[^\[\]\r\n]*$",
                description="What this task answers, e.g. a DJEN communication id.",
            ),
        ] = "",
        stageId: Annotated[
            str,
            Field(
                pattern=r"^\d*$",
                description="Stage to move the lawsuit to after creation (advbox_list_task_filters stages).",
            ),
        ] = "",
        subStageId: SubStageId = "",
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
                raise tool_error("ADVBOX", InputError("attachment content is not valid base64")) from None
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
            if subStageId and not stageId:
                raise ValueError("subStageId needs stageId")
            stage = {"stage_id": stageId, "sub_stage_id": subStageId} if stageId else {}
            if dryRun:
                return client.preview_task(options, files, dedupe_key=dedupeKey, **stage)
            return client.create_task(options, files, dedupe_key=dedupeKey, **stage)

        return call(run) if dryRun else write(run)

    @server.tool(
        name="advbox_update_task",
        title="Update ADVBOX task",
        description="Edit a PENDING task; omitted fields stay. IMPORTANT: ADVBOX replaces the task, so the result has a NEW postId (replacedPostId no longer exists). Existing attachments stay. Use expectComments to avoid editing the wrong task. Unremoved files go to removeErrors. If unconfirmed the error says the task MAY have been replaced: search the lawsuit's tasks before retrying. Needs ADVBOX_ALLOW_WRITES.",
        annotations=DESTRUCTIVE,
    )
    def update_task(
        postId: Id,
        comments: Annotated[
            str | None, Field(max_length=20000, description="Replaces the whole comment; omit to keep.")
        ] = None,
        appendComments: Annotated[
            str | None,
            Field(max_length=5000, description="Paragraph added to the end of the comment."),
        ] = None,
        date: Annotated[Date | None, Field(description="Omit to keep.")] = None,
        hour: Annotated[Hour | None, Field(description="'' clears; omit to keep.")] = None,
        deadline: Annotated[OptionalDate | None, Field(description="'' clears; omit to keep.")] = None,
        guests: Annotated[
            UserIds | None,
            Field(description="User IDs, JSON list, e.g. [961]; omit to keep."),
        ] = None,
        attachments: Annotated[
            list[Attachment],
            Field(
                max_length=MAX_ATTACHMENTS,
                description="Inline base64 files; max 5 with attachmentFiles.",
            ),
        ] = [],
        attachmentFiles: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=128)]],
            Field(max_length=5, description='savedAs names, e.g. ["ata.pdf"].'),
        ] = [],
        removeFiles: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=255)]],
            Field(max_length=10, description="Attachment names to delete."),
        ] = [],
        expectComments: Annotated[
            str,
            Field(
                max_length=255,
                description="Text the current comment must contain, else refused.",
            ),
        ] = "",
        dryRun: Annotated[bool, Field(description="Return the body without sending. Use first.")] = False,
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
                raise tool_error("ADVBOX", InputError("attachment content is not valid base64")) from None
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

    @server.tool(
        name="advbox_complete_task",
        title="Complete ADVBOX task",
        description="Mark a PENDING task completed. Fails unless ADVBOX confirms. Needs ADVBOX_ALLOW_WRITES.",
        annotations=DESTRUCTIVE,
    )
    def complete_task(postId: Id, expectComments: ExpectComments = "", dryRun: DryRun = False) -> dict[str, Any]:
        def run():
            return client.complete_task(postId, expect_comments=expectComments, dry_run=dryRun)

        return call(run) if dryRun else write(run)

    @server.tool(
        name="advbox_delete_task",
        title="Delete ADVBOX task",
        description="Soft-delete a task (scope for recurring tasks); it leaves listings, attachments survive. Destructive: confirm with the user first. Fails unless ADVBOX confirms. Needs ADVBOX_ALLOW_WRITES.",
        annotations=DESTRUCTIVE,
    )
    def delete_task(
        postId: Id,
        scope: Annotated[
            Literal["this", "future", "all"],
            Field(description="'this' only, or 'future'/'all' recurring copies."),
        ] = "this",
        comments: Annotated[str, Field(max_length=2000, description="Note recorded with the deletion.")] = "",
        expectComments: ExpectComments = "",
        dryRun: DryRun = False,
    ) -> dict[str, Any]:
        def run():
            return client.delete_task(
                postId, scope=scope, comments=comments, expect_comments=expectComments, dry_run=dryRun
            )

        return call(run) if dryRun else write(run)

    @server.tool(
        name="advbox_update_lawsuit_stage",
        title="Change ADVBOX lawsuit stage",
        description="Move a lawsuit to another stage (Fase; ids from advbox_list_task_filters stages) and sub-stage (Etapa); nothing else changes. Unknown ids are refused before sending. Returns previous and new stage: call with previous to undo. Confirmed by re-reading the lawsuit, otherwise fails saying NOT confirmed. dryRun also returns the stage's subStageOptions. Needs ADVBOX_ALLOW_WRITES.",
        annotations=REPLACE,
    )
    def update_lawsuit_stage(
        lawsuitId: Id,
        stageId: StageId,
        subStageId: SubStageId = "",
        dryRun: Annotated[bool, Field(description="Return the plan without sending. Use first.")] = False,
    ) -> dict[str, Any]:
        def run():
            return client.update_lawsuit_stage(lawsuitId, stageId, subStageId, dry_run=dryRun)

        return call(run) if dryRun else write(run)

    return server


def main():
    load_env_file()
    serve(create_server(), on_exit=client.close)


if __name__ == "__main__":
    main()
