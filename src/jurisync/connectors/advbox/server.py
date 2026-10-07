"""ADVBOX MCP server: customers, lawsuits, activities, files, and guarded task writes."""

import base64
import binascii
import json
from typing import Annotated, Any, Literal

from curl_cffi.requests.exceptions import RequestException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from jurisync.connectors.advbox.client import AdvboxClient
from jurisync.connectors.advbox.documents import AdvboxDocuments
from jurisync.connectors.advbox.session import require_writes
from jurisync.core.mcp import READ_ONLY, load_env_file, run_stdio, tool_errors

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
    description="Retrieve the complete lawsuit report: phase, court (comarca/vara), electronic system, action type, responsible, intimações (court notices with full text), and task/movement history with dates and responsibles. Read-only.",
    annotations=READ_ONLY,
)
def lawsuit_report(
    lawsuitId: Id,
    includeMovements: bool = True,
    includePublications: bool = True,
    includeTasks: bool = True,
    includeTransactions: bool = True,
) -> dict[str, Any]:
    return call(
        lambda: client.lawsuit_report(
            lawsuitId,
            movements=includeMovements,
            publications=includePublications,
            tasks=includeTasks,
            transactions=includeTransactions,
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


@server.tool(
    name="advbox_create_task",
    title="Create ADVBOX task",
    description="Create a task on a lawsuit with a task-type ID, responsible guest IDs, dates, and comments. Optional workflow linkage and attachments require account configuration. Available only when ADVBOX_ALLOW_WRITES=true.",
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
    attachments: Annotated[list[Attachment], Field(max_length=5)] = [],
) -> dict[str, Any]:
    def run():
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
        return client.create_task(
            {
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
            },
            files,
        )

    return write(run)


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
    run_stdio(server, on_exit=client.close)


if __name__ == "__main__":
    main()
