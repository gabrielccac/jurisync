"""ADVBOX MCP server: customers, lawsuits, activities, files, and guarded task writes."""

import base64
import binascii
import json
import mimetypes
from typing import Annotated, Any, Literal

from curl_cffi.requests.exceptions import RequestException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.connectors.advbox.parsers import compact_lawsuit_report
from jurisync.connectors.advbox.session import require_writes
from jurisync.core.documents_dir import resolve_saved_document
from jurisync.core.mcp import READ_ONLY, load_env_file, serve, tool_errors

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
        pattern=r"^(\d{2}/\d{2}/\d{4})?$",
        description="Date as DD/MM/YYYY (e.g. '16/10/2026', not ISO); empty string means none.",
    ),
]
Date = Annotated[
    str,
    Field(pattern=r"^\d{2}/\d{2}/\d{4}$", description="Date as DD/MM/YYYY (e.g. '16/10/2026', not ISO 2026-10-16)."),
]
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

client = AdvboxClient()
documents = AdvboxDocuments(client)
server = MCPServer("advbox", version="1.0.0")


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
    description="Search and paginate lawsuits in ADVBOX. Supports sorting (created = lawsuits.created_at, movement = date_last_movement — often unpopulated upstream, prefer created) and a created-date range (DD/MM/YYYY). Read-only.",
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
        Field(description="Created-date range start, DD/MM/YYYY (e.g. '01/10/2026'); empty string for none."),
    ] = "",
    dateUntil: Annotated[
        OptionalDate, Field(description="Created-date range end, DD/MM/YYYY; empty string for none.")
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
            dateFrom=dateFrom,
            dateUntil=dateUntil,
        )
    )


@server.tool(
    name="advbox_list_activities",
    title="List ADVBOX activities",
    description="List assigned activities (Atividades) in ADVBOX with responsible users, start and deadline dates, important/urgent/overdue flags, and linked customer, lawsuit, and task-template IDs. Defaults to pending activities. Read-only.",
    annotations=READ_ONLY,
)
def list_activities(
    page: Page = 1,
    pageSize: PageSize = 50,
    search: Search = "",
    completed: Annotated[
        Literal["pendentes", "concluidas"],
        Field(
            description="'pendentes' (default) lists open activities. 'concluidas' is meant to list completed ones, but in a live test it returned the same pending rows: do not rely on it until fixed."
        ),
    ] = "pendentes",
    responsible: FilterUserIds = None,
    dateFrom: Annotated[
        Date | None, Field(description="Start date DD/MM/YYYY, e.g. '15/10/2026'. ISO is refused.")
    ] = None,
    dateTo: Annotated[Date | None, Field(description="End date DD/MM/YYYY, e.g. '15/10/2026'.")] = None,
) -> dict[str, Any]:
    return call(
        lambda: client.list_activities(
            page=page,
            pageSize=pageSize,
            search=search,
            completed=completed,
            responsibleIds=responsible or [],
            dateFrom=dateFrom or "",
            dateTo=dateTo or "",
        )
    )


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
    dateFrom: Annotated[
        Date | None, Field(description="Start date DD/MM/YYYY, e.g. '01/10/2026'. ISO is refused.")
    ] = None,
    dateTo: Annotated[Date | None, Field(description="End date DD/MM/YYYY, e.g. '15/10/2026'.")] = None,
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
            dateFrom=dateFrom or "",
            dateTo=dateTo or "",
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
    name="advbox_find_tasks",
    title="Find pending ADVBOX tasks",
    description="Find PENDING tasks (query of at least 3 characters; responsible is a JSON list of user IDs) whose title, comment, client or process number contains a text (for example a process number or an atom name), across all members unless responsible IDs are given. Returns compact records with the post ID, title, comment, start, deadline, client and responsibles; the post ID is what an edit needs. It does not list completed tasks and does not match DJEN IDs written in comments: for a lawsuit's full history (including completed tasks) use advbox_lawsuit_report with taskSearch. Read-only.",
    annotations=READ_ONLY,
)
def find_tasks(
    query: Annotated[
        str,
        Field(
            min_length=3,
            max_length=255,
            description="Text to find, at least 3 characters (an empty query cannot list everything).",
        ),
    ],
    responsible: FilterUserIds = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max tasks returned, 1 to 100; default 25.")] = 25,
) -> dict[str, Any]:
    return call(lambda: client.find_tasks(query, responsible=responsible, limit=limit))


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


class Attachment(BaseModel):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    contentBase64: Annotated[str, Field(min_length=1)]
    contentType: Annotated[str, Field(max_length=127)] = "application/pdf"


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ATTACHMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".mp3", ".txt"}


def _saved_files(names):
    """Read files that another tool saved in the shared documents folder (confined to that folder)."""
    files = []
    for name in names:
        path = resolve_saved_document(name)
        if path.suffix.lower() not in ATTACHMENT_SUFFIXES:
            raise ValueError(f"Attachment {name} is not a supported type ({', '.join(sorted(ATTACHMENT_SUFFIXES))})")
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


@server.tool(
    name="advbox_create_task",
    title="Create ADVBOX task",
    description="Create a task on a lawsuit with a task-type ID (tasksId), responsible guest IDs (guests: a JSON list of 1 or more user IDs from advbox_list_members; a task cannot be created with no responsible), a date in DD/MM/YYYY, and comments. Attachments can be given inline as base64 (attachments) or by file name from the shared documents folder (attachmentFiles, the savedAs names returned by pje_read_documents with save=true); at most 5 files in total. With dryRun=true nothing is sent: the task body and files are validated and returned. Creating requires ADVBOX_ALLOW_WRITES=true; attachments also need ADVBOX_UPLOAD_FOLDER.",
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
            "date": date,
            "hour": hour,
            "deadline": deadline,
            "comments": comments,
        }
        if dryRun:
            return client.preview_task(options, files)
        return client.create_task(options, files)

    return call(run) if dryRun else write(run)


@server.tool(
    name="advbox_update_task",
    title="Update ADVBOX task",
    description="Edit a PENDING task the way the ADVBOX screen does: change its comment (comments replaces, appendComments adds a paragraph), date, hour, deadline or guests, and add or remove attachments. Fields left out stay as they are; hour or deadline set to an empty string clear them. IMPORTANT: ADVBOX replaces the task, so the result has a NEW postId (replacedPostId is the old one, which no longer exists). Existing attachments are kept; new files come from attachmentFiles (names saved by pje_read_documents) or base64 attachments; removeFiles names attachments to delete. Use expectComments (a text the current comment must contain) so the edit is refused if the post ID points at a different task. With dryRun=true nothing is sent and the body that would be sent is returned. Do not edit completed tasks. Requires ADVBOX_ALLOW_WRITES=true.",
    annotations=WRITE,
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
        Date | None, Field(description="New date DD/MM/YYYY, e.g. '20/10/2026'. Leave empty to keep it.")
    ] = None,
    hour: Annotated[
        Hour | None, Field(description="New time HH:MM, 24h. Empty string clears it; leave out to keep it.")
    ] = None,
    deadline: Annotated[
        OptionalDate | None, Field(description="New deadline DD/MM/YYYY. Empty string clears it; leave out to keep it.")
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
            "date": date,
            "hour": hour,
            "deadline": deadline,
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
    description="Mark a task completed by its post ID. Available only when ADVBOX_ALLOW_WRITES=true.",
    annotations=WRITE,
)
def complete_task(postId: Id) -> dict[str, Any]:
    return write(lambda: client.complete_task(postId))


@server.tool(
    name="advbox_delete_task",
    title="Delete ADVBOX task",
    description="Soft-delete a task by its post ID (scope this/future/all for recurring tasks). The task leaves listings; attachments survive. Available only when ADVBOX_ALLOW_WRITES=true.",
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
) -> dict[str, Any]:
    return write(lambda: client.delete_task(postId, scope=scope, comments=comments))


def main():
    load_env_file()
    serve(server, on_exit=client.close)


if __name__ == "__main__":
    main()
