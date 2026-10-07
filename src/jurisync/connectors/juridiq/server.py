"""Juridiq MCP server. Write tools are registered only when JURIDIQ_ALLOW_WRITES=true."""

import os
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from curl_cffi.requests.exceptions import RequestException
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from jurisync.connectors.juridiq.api import JuridiqApi, JuridiqError
from jurisync.core.mcp import READ_ONLY, load_env_file, run_stdio, tool_errors

ADDITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
ERRORS = (JuridiqError, ValueError, PermissionError, OSError, RequestException)

Text = Annotated[str, Field(min_length=1)]
OneOrMany = Text | Annotated[list[Text], Field(min_length=1)]
Limit = Annotated[int, Field(ge=1, le=100)]
Id = UUID


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LawSuitPerson(Strict):
    id: UUID | None = None
    name: str
    personOrigin: Literal["Cliente", "Requerida"]
    document: str | None = None


class CustomField(Strict):
    fieldId: UUID
    value: (
        Annotated[str, Field(max_length=10000)]
        | int
        | float
        | Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=100)]
        | None
    )


class LawSuitAction(Strict):
    id: UUID | None = None
    name: str


Action = LawSuitAction | Literal[""]
Instance = Annotated[int, Field(ge=1, le=3)]
CustomFields = Annotated[list[CustomField], Field(max_length=100)]


class LawSuitCreate(Strict):
    processNumber: str | None = None
    serviceStep: str
    responsibleIds: list[UUID]
    persons: list[LawSuitPerson]
    officeId: UUID | None = None
    valueOfCause: str | None = None
    valueOfFees: str | None = None
    percentageOfFees: str | None = None
    requerimentDate: str | None = None
    protocolNumber: str | None = None
    annotations: str | None = None
    originatingProcess: str | None = None
    actionGroup: Action | None = None
    typeAction: Action | None = None
    currentInstance: Instance | None = None
    instance: Instance | None = None
    court: str | None = None
    tags: list[UUID] | None = None
    degreeOfRisk: UUID | Literal[""] | None = None
    isPrivate: bool | None = None
    isSecret: bool | None = None
    isProcessAdministrative: bool | None = None
    linkedLawSuitId: list[UUID] | None = None
    processUrl: str | None = None
    courtBranch: str | None = None
    precatoryNumbers: list[str] | None = None
    status: str | None = None
    publicationId: str | None = None
    createdByAutomation: bool | None = None
    author: str | None = None
    defendant: str | None = None
    periciaResult: str | None = None
    periciaExpertName: str | None = None
    periciaExpertType: str | None = None
    condemnationAmount: str | None = None
    periciaExpertHonoraries: str | None = None
    valueHonorariesSuccession: str | None = None
    petition: str | None = None
    valuePetition: str | None = None
    corretor: str | None = None
    code: str | None = None
    consultingId: UUID | None = None
    customFields: CustomFields | None = None


class LawSuitUpdate(Strict):
    serviceStep: str | None = None
    responsibleIds: list[UUID] | None = None
    officeId: UUID | None = None
    processNumber: str | None = None
    valueOfCause: str | None = None
    valueOfFees: str | None = None
    percentageOfFees: str | None = None
    requerimentDate: str | None = None
    protocolNumber: str | None = None
    annotations: str | None = None
    originatingProcess: str | None = None
    persons: list[LawSuitPerson] | None = None
    actionGroup: Action | None = None
    typeAction: Action | None = None
    degreeOfRiskId: UUID | Literal[""] | None = None
    tags: list[UUID] | None = None
    court: str | None = None
    currentInstance: Instance | None = None
    instance: Instance | None = None
    courtLawSuitDate: str | None = None
    status: str | None = None
    linkedLawSuitId: list[UUID] | None = None
    isPrivate: bool | None = None
    isSecret: bool | None = None
    processUrl: str | None = None
    courtBranch: str | None = None
    precatoryNumbers: list[str] | None = None
    author: str | None = None
    defendant: str | None = None
    periciaResult: str | None = None
    periciaExpertName: str | None = None
    periciaExpertType: str | None = None
    condemnationAmount: str | None = None
    periciaExpertHonoraries: str | None = None
    valueHonorariesSuccession: str | None = None
    petition: str | None = None
    valuePetition: str | None = None
    corretor: str | None = None
    code: str | None = None
    consultingColumnId: UUID | None = None
    consultingId: UUID | None = None
    customFields: CustomFields | None = None


class TaskCreate(Strict):
    title: Text
    priority: Text
    columnId: UUID
    initialDate: str
    description: Any = None
    finalDate: str | None = None
    initialHour: str | None = None
    finalHour: str | None = None
    lawSuitId: UUID | None = None
    responsiblesId: list[UUID] | None = None
    officeId: UUID | None = None
    isPrivate: bool | None = None
    tags: list[UUID] | None = None
    points: int | float | None = None
    needValidation: bool | None = None
    isValidated: bool | None = None
    validatorsId: list[UUID] | None = None
    personIds: list[UUID] | None = None
    estimatedTimeMinutes: Annotated[int, Field(ge=0)] | None = None
    createSheet: bool | None = None
    consultingId: UUID | None = None
    customFields: CustomFields | None = None


class TaskUpdate(Strict):
    title: Text | None = None
    priority: Text | None = None
    status: Text | None = None
    initialDate: str | None = None
    lawSuitId: UUID | None = None
    responsiblesId: list[UUID] | None = None
    isArchived: bool | None = None
    isPrivate: bool | None = None
    officeId: UUID | None = None
    description: Any = None
    finalDate: str | None = None
    initialHour: str | None = None
    finalHour: str | None = None
    currentIndex: int | float | None = None
    tags: list[UUID] | None = None
    points: int | float | None = None
    needValidation: bool | None = None
    isValidated: bool | None = None
    validatorsId: list[UUID] | None = None
    personIds: list[UUID] | None = None
    estimatedTimeMinutes: Annotated[int, Field(ge=0)] | None = None
    columnId: UUID | None = None
    consultingId: UUID | None = None
    customFields: CustomFields | None = None


class EventCreate(Strict):
    title: Text
    date: date
    initialHour: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    finalHour: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    responsibleIds: list[UUID]
    lawSuitId: UUID | None = None
    personId: UUID | None = None
    description: str | None = None


def _payload(model):
    """Send only the fields the caller supplied, as the Node connector did."""
    return model.model_dump(mode="json", exclude_unset=True)


def create_server(allow_writes=None, api=None):
    allow_writes = os.environ.get("JURIDIQ_ALLOW_WRITES") == "true" if allow_writes is None else allow_writes
    api = api or JuridiqApi()
    server = MCPServer("juridiq", version="1.0.0")

    def call(handler):
        with tool_errors("Juridiq", *ERRORS):
            value = handler()
        # Lists and raw text are wrapped as the Node SDK did: structured content must be an object.
        return value if isinstance(value, dict) else {"result": value}

    def read(name, title, description):
        return server.tool(name=name, title=title, description=description, annotations=READ_ONLY)

    def write(name, title, description, annotations):
        if allow_writes:
            return server.tool(name=name, title=title, description=description, annotations=annotations)
        return lambda fn: fn

    @read(
        "juridiq_list_pending_publications",
        "List pending Juridiq publications",
        "List unread Juridiq publications for the configured account. Read-only.",
    )
    def list_pending_publications(maxResults: Annotated[int, Field(ge=1, le=500)] = 100) -> dict[str, Any]:
        return call(lambda: api.all_unread_publications(maxResults=maxResults))

    @read(
        "juridiq_list_publications",
        "List Juridiq publications",
        "List Juridiq publications using the API's native filters. To find recent records, request an appropriate date range or order and let the agent interpret the returned dates. Read-only.",
    )
    def list_publications(
        page: Annotated[int, Field(ge=1)] = 1,
        limit: Limit = 50,
        orderBy: Literal["asc", "desc"] | None = None,
        keySearch: Text | None = None,
        courtOrDiary: Annotated[str, Field(min_length=1, max_length=200)] | None = None,
        isHandled: bool | None = None,
        monitoredTermId: Text | None = None,
        start: Text | None = None,
        end: Text | None = None,
    ) -> dict[str, Any]:
        return call(
            lambda: api.publication_page(
                page=page,
                limit=limit,
                unreadOnly=False,
                orderBy=orderBy,
                keySearch=keySearch,
                courtOrDiary=courtOrDiary,
                isHandled=isHandled,
                monitoredTermId=monitoredTermId,
                start=start,
                end=end,
            )
        )

    @read("juridiq_find_lawsuit", "Find Juridiq lawsuit", "Find lawsuits by exact process number. Read-only.")
    def find_lawsuit(processNumber: Text) -> dict[str, Any]:
        return call(lambda: api.find_lawsuits(processNumber))

    @read(
        "juridiq_list_lawsuits",
        "List Juridiq lawsuits",
        "List lawsuits available to the configured account, up to maxResults. Read-only. The API response determines which records are open; do not assume an open-status filter that was not returned.",
    )
    def list_lawsuits(maxResults: Annotated[int, Field(ge=1, le=500)] = 100) -> dict[str, Any]:
        return call(lambda: api.all_lawsuits(maxResults=maxResults))

    @read("juridiq_get_lawsuit", "Get Juridiq lawsuit", "Retrieve a Juridiq lawsuit by ID. Read-only.")
    def get_lawsuit(lawSuitId: Text) -> dict[str, Any]:
        return call(lambda: api.get_lawsuit(lawSuitId))

    @write(
        "juridiq_create_lawsuit",
        "Create Juridiq lawsuit",
        "Create a lawsuit in the configured Juridiq account. Required payload fields are serviceStep, responsibleIds (UUIDs), and persons with name/personOrigin. personOrigin must be Cliente or Requerida. Available only when JURIDIQ_ALLOW_WRITES=true.",
        ADDITIVE,
    )
    def create_lawsuit(payload: LawSuitCreate) -> dict[str, Any]:
        return call(lambda: api.create_lawsuit(_payload(payload)))

    @write(
        "juridiq_update_lawsuit",
        "Update Juridiq lawsuit",
        "Update a lawsuit in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_lawsuit(lawSuitId: Id, patch: LawSuitUpdate) -> dict[str, Any]:
        return call(lambda: api.update_lawsuit(str(lawSuitId), _payload(patch)))

    @write(
        "juridiq_delete_lawsuit",
        "Delete Juridiq lawsuit",
        "Delete only the explicitly supplied lawsuit in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_lawsuit(lawSuitId: Id) -> dict[str, Any]:
        return call(lambda: api.delete_lawsuit(str(lawSuitId)))

    @read(
        "juridiq_list_lawsuit_movements",
        "List Juridiq lawsuit movements",
        "List movements attached to a Juridiq lawsuit, including content, date, instance and tags. Read-only; useful for comparing a publication with its Juridiq process movement.",
    )
    def list_lawsuit_movements(
        lawSuitId: Text,
        tagId: Text | None = None,
        content: Text | None = None,
        instance: Annotated[int, Field(ge=1)] | None = None,
    ) -> dict[str, Any]:
        return call(lambda: api.lawsuit_movements(lawSuitId, tagId=tagId, content=content, instance=instance))

    @read(
        "juridiq_get_publication",
        "Get Juridiq publication",
        "Retrieve the complete details of one Juridiq publication by ID, including content, dates, process number, official diary and read state. Read-only.",
    )
    def get_publication(publicationId: Text) -> dict[str, Any]:
        return call(lambda: api.get_publication(publicationId))

    @read(
        "juridiq_list_events",
        "List Juridiq calendar events",
        "List calendar items/events with process, person, responsible, date-range, completion, type and tag filters. Use lawSuitId to inspect events related to a process before creating anything. Read-only.",
    )
    def list_events(
        responsible: OneOrMany | None = None,
        responsiblesMetrics: OneOrMany | None = None,
        personId: Text | None = None,
        lawSuitId: Text | None = None,
        dateType: Literal["initialDate", "finalDate", "all"] | None = None,
        start: Text | None = None,
        end: Text | None = None,
        statusFilter: Literal["all", "completed", "pending"] = "all",
        fullQuery: bool | None = None,
        includeFutureCount: bool | None = None,
        tag: OneOrMany | None = None,
        itemType: Literal["all", "tasks", "events", "audiences", "google"] = "all",
        taskPriority: OneOrMany | None = None,
        taskTag: OneOrMany | None = None,
        orderBy: Literal["asc", "desc"] | None = None,
        orderByField: Text | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        filters = dict(
            responsible=responsible,
            responsiblesMetrics=responsiblesMetrics,
            personId=personId,
            lawSuitId=lawSuitId,
            dateType=dateType,
            start=start,
            end=end,
            statusFilter=statusFilter,
            fullQuery=fullQuery,
            includeFutureCount=includeFutureCount,
            tag=tag,
            itemType=itemType,
            taskPriority=taskPriority,
            taskTag=taskTag,
            orderBy=orderBy,
            orderByField=orderByField,
        )
        return call(lambda: api.events(page=page, limit=limit, **filters))

    @read(
        "juridiq_get_event",
        "Get Juridiq event",
        "Retrieve one calendar event by ID, including date, hours, linked process/person and responsibles. Read-only.",
    )
    def get_event(eventId: Text) -> dict[str, Any]:
        return call(lambda: api.get_event(eventId))

    @read(
        "juridiq_list_task_templates",
        "List Juridiq task templates",
        "List live task templates for the configured account. Read-only.",
    )
    def list_task_templates(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.task_templates(limit=limit))

    @read("juridiq_list_users", "List Juridiq users", "List users in the configured office. Read-only.")
    def list_users(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.users(limit=limit))

    @read("juridiq_list_tags", "List Juridiq tags", "List tags in the configured office. Read-only.")
    def list_tags(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.tags(limit=limit))

    @read(
        "juridiq_list_task_columns",
        "List Juridiq task columns",
        "List task board columns in the configured office. Read-only.",
    )
    def list_task_columns(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.task_columns(limit=limit))

    @read(
        "juridiq_list_tasks",
        "List Juridiq tasks",
        "List tasks using the API's filters. For publication deduplication, filter by lawSuitId and compare the semantic obligation, not only the task title. Read-only.",
    )
    def list_tasks(
        page: Annotated[int, Field(ge=1)] = 1,
        limit: Limit = 30,
        lawSuitId: Text | None = None,
        title: Text | None = None,
        responsible: OneOrMany | None = None,
        priority: OneOrMany | None = None,
        isHome: bool | None = None,
        orderBy: Literal["asc", "desc"] | None = None,
        orderByField: Text | None = None,
        isUniqueResponsible: bool | None = None,
        isArchived: bool | None = None,
        isPrivate: bool | None = None,
        start: Text | None = None,
        end: Text | None = None,
        tag: OneOrMany | None = None,
        dateMethod: Literal["all", "initialDate", "finalDate", "completedAt", "subtaskFinalDate"] | None = None,
        personId: Text | None = None,
        points: Text | int | float | None = None,
        parentTaskId: Text | None = None,
        taskColumnId: Text | None = None,
        folderId: Text | None = None,
        boardId: Text | None = None,
        taskKanbanColumnIds: OneOrMany | None = None,
        status: OneOrMany | None = None,
    ) -> dict[str, Any]:
        options = {key: value for key, value in locals().items()}
        return call(lambda: api.tasks(**options))

    @read(
        "juridiq_list_tasks_kanban",
        "List Juridiq Kanban tasks",
        "List tasks grouped by Kanban status. Use for board-oriented questions; use juridiq_list_tasks for filterable task records and deduplication. Read-only.",
    )
    def list_tasks_kanban(
        page: Annotated[int, Field(ge=1)] = 1,
        limit: Limit = 30,
        lawSuitId: Text | None = None,
        title: Text | None = None,
        responsible: OneOrMany | None = None,
        priority: OneOrMany | None = None,
        isHome: bool | None = None,
        orderBy: Literal["asc", "desc"] | None = None,
        orderByField: Text | None = None,
        isUniqueResponsible: bool | None = None,
        isArchived: bool | None = None,
        isPrivate: bool | None = None,
        start: Text | None = None,
        end: Text | None = None,
        tag: OneOrMany | None = None,
        dateMethod: Literal["all", "initialDate", "finalDate", "completedAt", "subtaskFinalDate"] | None = None,
        personId: Text | None = None,
        points: Text | int | float | None = None,
        parentTaskId: Text | None = None,
        taskColumnId: Text | None = None,
        folderId: Text | None = None,
        boardId: Text | None = None,
        taskKanbanColumnIds: OneOrMany | None = None,
        status: OneOrMany | None = None,
    ) -> dict[str, Any]:
        options = {key: value for key, value in locals().items()}
        return call(lambda: api.kanban_tasks(**options))

    @read("juridiq_get_task", "Get Juridiq task", "Retrieve a task by ID. Read-only.")
    def get_task(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.get_task(taskId))

    @read(
        "juridiq_get_task_history",
        "Get Juridiq task history",
        "Retrieve the history of a task, including actions, dates, users and Kanban transitions. Read-only.",
    )
    def get_task_history(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.task_history(taskId))

    @read(
        "juridiq_list_task_comments",
        "List Juridiq task comments",
        "List comments and replies for a task. Use to inspect human notes before deciding whether a publication is already covered. Read-only.",
    )
    def list_task_comments(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.task_comments(taskId))

    @write(
        "juridiq_create_task",
        "Create Juridiq task",
        "Create a task in the configured Juridiq account. Required payload fields are title, priority, columnId (a Kanban column UUID from juridiq_list_task_columns), and initialDate. Use responsiblesId, not responsibleIds. Available only when JURIDIQ_ALLOW_WRITES=true.",
        ADDITIVE,
    )
    def create_task(payload: TaskCreate) -> dict[str, Any]:
        return call(lambda: api.create_task(_payload(payload)))

    @write(
        "juridiq_update_task",
        "Update Juridiq task",
        "Update a task in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_task(taskId: Id, patch: TaskUpdate) -> dict[str, Any]:
        return call(lambda: api.update_task(str(taskId), _payload(patch)))

    @write(
        "juridiq_delete_task",
        "Delete Juridiq task",
        "Delete only the explicitly supplied task in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_task(taskId: Id) -> dict[str, Any]:
        return call(lambda: api.delete_task(str(taskId)))

    @write(
        "juridiq_create_event",
        "Create Juridiq event",
        "Create a calendar event in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        ADDITIVE,
    )
    def create_event(payload: EventCreate) -> dict[str, Any]:
        return call(lambda: api.create_event(_payload(payload)))

    @write(
        "juridiq_mark_publications_read",
        "Mark Juridiq publications read",
        "Mark explicitly supplied publication IDs as read in the configured Juridiq account. Available only when JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def mark_publications_read(
        publicationIds: Annotated[list[Text], Field(min_length=1, max_length=50)],
    ) -> dict[str, Any]:
        return call(lambda: api.mark_publications_read(publicationIds))

    return server


def main():
    load_env_file()
    run_stdio(create_server())


if __name__ == "__main__":
    main()
