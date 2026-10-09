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

Id = Annotated[str, Field(pattern=r"^\d+$")]
Page = Annotated[int, Field(ge=1)]
PageSize = Annotated[int, Field(ge=1, le=100)]
Search = Annotated[str, Field(max_length=255)]
OptionalDate = Annotated[str, Field(pattern=r"^(\d{2}/\d{2}/\d{4})?$")]
Date = Annotated[str, Field(pattern=r"^\d{2}/\d{2}/\d{4}$")]

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
    sortBy: Literal["parties", "type", "created", "movement"] = "parties",
    sortDir: Literal["asc", "desc"] = "asc",
    dateFrom: OptionalDate = "",
    dateUntil: OptionalDate = "",
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
    completed: Literal["pendentes", "concluidas"] = "pendentes",
    responsible: Annotated[list[Annotated[int, Field(gt=0)]] | None, Field(max_length=50)] = None,
    dateFrom: Date | None = None,
    dateTo: Date | None = None,
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
    period: Literal["today", "3d", "7d", "30d"] | None = None,
    dateFrom: Date | None = None,
    dateTo: Date | None = None,
    tribunal: Annotated[str, Field(max_length=40)] = "",
    processNumber: Annotated[str, Field(pattern=r"^(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})?$")] = "",
    responsible: Annotated[int, Field(gt=0)] | None = None,
    page: Page = 1,
    pageSize: Annotated[int, Field(ge=1, le=200)] = 50,
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
    description="Find PENDING tasks whose title, comment, client or process number contains a text (for example a process number or an atom name), across all members unless responsible IDs are given. Returns compact records with the post ID, title, comment, start, deadline, client and responsibles; the post ID is what an edit needs. It does not list completed tasks and does not match DJEN IDs written in comments: for a lawsuit's full history (including completed tasks) use advbox_lawsuit_report with taskSearch. Read-only.",
    annotations=READ_ONLY,
)
def find_tasks(
    query: Annotated[str, Field(min_length=3, max_length=255)],
    responsible: Annotated[list[Annotated[int, Field(gt=0)]] | None, Field(max_length=100)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
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
    includeMovements: bool = True,
    includePublications: bool = True,
    includeTasks: bool = True,
    includeTransactions: bool = True,
    taskSearch: Annotated[str, Field(max_length=255)] = "",
    maxTasks: Annotated[int, Field(ge=0, le=500)] = 0,
    maxNotices: Annotated[int, Field(ge=0, le=100)] = 0,
    maxTextChars: Annotated[int, Field(ge=0, le=20000)] = 0,
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
def list_files(customerId: Id, folder: Annotated[str, Field(max_length=255)] = "") -> dict[str, Any]:
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
    url: Annotated[str, Field(min_length=1, max_length=2048)],
    filename: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
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
    folder: Annotated[str, Field(min_length=1, max_length=255)],
    maxFiles: Annotated[int, Field(ge=1, le=100)] = 50,
    ocr: bool = True,
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
    description="Create a task on a lawsuit with a task-type ID, responsible guest IDs, dates, and comments. Attachments can be given inline as base64 (attachments) or by file name from the shared documents folder (attachmentFiles, the savedAs names returned by pje_read_documents with save=true); at most 5 files in total. With dryRun=true nothing is sent: the task body and files are validated and returned. Creating requires ADVBOX_ALLOW_WRITES=true; attachments also need ADVBOX_UPLOAD_FOLDER.",
    annotations=WRITE,
)
def create_task(
    lawsuitId: Id,
    tasksId: Id,
    guests: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1, max_length=20)],
    date: Date,
    workflowId: Annotated[str, Field(pattern=r"^\d*$")] = "",
    workflowSequence: Annotated[str, Field(pattern=r"^\d*$")] = "1",
    workflowLimit: Annotated[str, Field(pattern=r"^\d*$")] = "",
    hour: Annotated[str, Field(pattern=r"^(\d{2}:\d{2})?$")] = "",
    deadline: OptionalDate = "",
    comments: Annotated[str, Field(max_length=2000)] = "",
    attachments: Annotated[list[Attachment], Field(max_length=MAX_ATTACHMENTS)] = [],
    attachmentFiles: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=5)] = [],
    dryRun: bool = False,
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
    comments: Annotated[str, Field(max_length=20000)] | None = None,
    appendComments: Annotated[str, Field(max_length=5000)] | None = None,
    date: Date | None = None,
    hour: Annotated[str, Field(pattern=r"^(\d{2}:\d{2})?$")] | None = None,
    deadline: OptionalDate | None = None,
    guests: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1, max_length=20)] | None = None,
    attachments: Annotated[list[Attachment], Field(max_length=MAX_ATTACHMENTS)] = [],
    attachmentFiles: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(max_length=5)] = [],
    removeFiles: Annotated[list[Annotated[str, Field(min_length=1, max_length=255)]], Field(max_length=10)] = [],
    expectComments: Annotated[str, Field(max_length=255)] = "",
    dryRun: bool = False,
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
    postId: Id, scope: Literal["this", "future", "all"] = "this", comments: Annotated[str, Field(max_length=2000)] = ""
) -> dict[str, Any]:
    return write(lambda: client.delete_task(postId, scope=scope, comments=comments))


def main():
    load_env_file()
    serve(server, on_exit=client.close)


if __name__ == "__main__":
    main()
