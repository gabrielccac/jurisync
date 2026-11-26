"""Juridiq MCP server. Write tools are registered only when JURIDIQ_ALLOW_WRITES=true."""

import inspect
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jurisync.connectors.juridiq.api import JuridiqApi
from jurisync.core import credentials
from jurisync.core.mcp import DESTRUCTIVE, READ_ONLY, WRITE, JurisyncServer, load_env_file, serve, tool_errors

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


TaskPriority = Literal["Baixa", "Média", "Alta"]
TaskStatus = Literal["Pendente", "Em progresso", "Concluída"]


class TaskCreate(Strict):
    title: Text
    priority: TaskPriority
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
    priority: TaskPriority | None = None
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


TagModule = Literal["processos", "tarefas", "andamentos", "pessoas", "atendimentos", "automacoes"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


class TagCreate(Strict):
    name: Text
    color: HexColor = "#0296F9"
    modules: Annotated[list[TagModule], Field(min_length=1)] = ["tarefas"]


class TagUpdate(Strict):
    name: Text | None = None
    color: HexColor | None = None
    modules: Annotated[list[TagModule], Field(min_length=1)] | None = None


class TaskSubtask(Strict):
    title: Text
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    isBusinessDay: bool | None = None
    daysForDue: Annotated[int, Field(ge=0)] | None = None
    daysForStart: Annotated[int, Field(ge=0)] | None = None
    points: int | float | None = None
    tags: list[UUID] | None = None
    responsiblesId: list[UUID] | None = None


class TaskTemplateCreate(Strict):
    title: Text
    description: str | None = None
    points: int | float | None = None
    columnId: UUID | None = None
    priority: TaskPriority | None = None
    daysForDue: Annotated[int, Field(ge=0)] | None = None
    daysForStart: Annotated[int, Field(ge=0)] | None = None
    isBusinessDay: bool | None = None
    createSheet: bool | None = None
    estimatedTimeMinutes: Annotated[int, Field(ge=0)] | None = None
    tags: list[UUID] | None = None
    responsiblesId: list[UUID] | None = None
    subtasks: list[TaskSubtask] | None = None


class TaskTemplateUpdate(Strict):
    title: Text | None = None
    description: str | None = None
    points: int | float | None = None
    columnId: UUID | None = None
    priority: TaskPriority | None = None
    daysForDue: Annotated[int, Field(ge=0)] | None = None
    daysForStart: Annotated[int, Field(ge=0)] | None = None
    isBusinessDay: bool | None = None
    createSheet: bool | None = None
    estimatedTimeMinutes: Annotated[int, Field(ge=0)] | None = None
    tags: list[UUID] | None = None
    responsiblesId: list[UUID] | None = None
    subtasks: list[TaskSubtask] | None = None


class EventCreate(Strict):
    title: Text
    date: date
    initialHour: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    finalHour: Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]
    responsibleIds: list[UUID]
    lawSuitId: UUID | None = None
    personId: UUID | None = None
    description: str | None = None


def _arguments():
    """The calling tool's own parameters. locals() would also return the closure's api and call, which the task
    filters reject."""
    frame = inspect.currentframe().f_back
    code = frame.f_code
    return {name: frame.f_locals[name] for name in code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]}


def _payload(model):
    """Send only the fields the caller supplied, as the Node connector did."""
    return model.model_dump(mode="json", exclude_unset=True)


def create_server(allow_writes=None, api=None):
    allow_writes = credentials.env("JURIDIQ_ALLOW_WRITES") == "true" if allow_writes is None else allow_writes
    api = api or JuridiqApi()
    server = JurisyncServer("juridiq")

    def call(handler):
        with tool_errors("Juridiq"):
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
        "List unread publications. Reads at most 20 pages of 100; truncated=true means some may be missing: narrow with juridiq_list_publications (date range). Read-only.",
    )
    def list_pending_publications(maxResults: Annotated[int, Field(ge=1, le=500)] = 100) -> dict[str, Any]:
        return call(lambda: api.unread_publications(maxResults=maxResults))

    @read(
        "juridiq_list_publications",
        "List Juridiq publications",
        "List publications (paged). For recent ones pass a date range or orderBy. Read-only.",
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
        "List lawsuits, up to maxResults. Not filtered by status: don't assume they are open. Read-only.",
    )
    def list_lawsuits(maxResults: Annotated[int, Field(ge=1, le=500)] = 100) -> dict[str, Any]:
        return call(lambda: api.all_lawsuits(maxResults=maxResults))

    @read("juridiq_get_lawsuit", "Get Juridiq lawsuit", "Get a lawsuit by ID. Read-only.")
    def get_lawsuit(lawSuitId: Text) -> dict[str, Any]:
        return call(lambda: api.get_lawsuit(lawSuitId))

    @write(
        "juridiq_create_lawsuit",
        "Create Juridiq lawsuit",
        "Create a lawsuit. Required: serviceStep, responsibleIds (UUIDs), persons (name, personOrigin Cliente|Requerida). If Juridiq doesn't confirm (timeout, connection error, 5xx) the lawsuit MAY have been created: check juridiq_find_lawsuit before retrying. Needs JURIDIQ_ALLOW_WRITES=true.",
        WRITE,
    )
    def create_lawsuit(payload: LawSuitCreate) -> dict[str, Any]:
        return call(lambda: api.create_lawsuit(_payload(payload)))

    @write(
        "juridiq_update_lawsuit",
        "Update Juridiq lawsuit",
        "Update a lawsuit; only supplied fields are sent. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_lawsuit(lawSuitId: Id, patch: LawSuitUpdate) -> dict[str, Any]:
        return call(lambda: api.update_lawsuit(str(lawSuitId), _payload(patch)))

    @write(
        "juridiq_delete_lawsuit",
        "Delete Juridiq lawsuit",
        "Delete only the given lawsuit. Preview with dryRun first. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_lawsuit(
        lawSuitId: Id,
        dryRun: Annotated[
            bool, Field(description="true: delete nothing, return the lawsuit that would be deleted.")
        ] = False,
        expectProcessNumber: Annotated[
            str,
            Field(
                max_length=40,
                description="Refuse unless the lawsuit has this process number (punctuation ignored). Empty: no check.",
            ),
        ] = "",
    ) -> dict[str, Any]:
        return call(
            lambda: api.delete_lawsuit(str(lawSuitId), expect_process_number=expectProcessNumber, dry_run=dryRun)
        )

    @read(
        "juridiq_list_lawsuit_movements",
        "List Juridiq lawsuit movements",
        "List a lawsuit's movements (content, date, instance, tags), e.g. to match a publication. Read-only.",
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
        "Get one publication by ID: full content, dates, process number, diary, read state. Read-only.",
    )
    def get_publication(publicationId: Text) -> dict[str, Any]:
        return call(lambda: api.get_publication(publicationId))

    @read(
        "juridiq_list_events",
        "List Juridiq calendar events",
        "List calendar items (paged). Check a process's events (lawSuitId) before creating any. Read-only.",
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
        filters = {
            "responsible": responsible,
            "responsiblesMetrics": responsiblesMetrics,
            "personId": personId,
            "lawSuitId": lawSuitId,
            "dateType": dateType,
            "start": start,
            "end": end,
            "statusFilter": statusFilter,
            "fullQuery": fullQuery,
            "includeFutureCount": includeFutureCount,
            "tag": tag,
            "itemType": itemType,
            "taskPriority": taskPriority,
            "taskTag": taskTag,
            "orderBy": orderBy,
            "orderByField": orderByField,
        }
        return call(lambda: api.events(page=page, limit=limit, **filters))

    @read(
        "juridiq_get_event",
        "Get Juridiq event",
        "Get one calendar event by ID. Read-only.",
    )
    def get_event(eventId: Text) -> dict[str, Any]:
        return call(lambda: api.get_event(eventId))

    @read(
        "juridiq_list_task_templates",
        "List Juridiq task templates",
        "List task templates. Read-only.",
    )
    def list_task_templates(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.task_templates(limit=limit))

    @read("juridiq_list_users", "List Juridiq users", "List office users. Read-only.")
    def list_users(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.users(limit=limit))

    @read("juridiq_list_tags", "List Juridiq tags", "List office tags. Read-only.")
    def list_tags(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.tags(limit=limit))

    @read(
        "juridiq_list_task_columns",
        "List Juridiq task columns",
        "List task board columns. Read-only.",
    )
    def list_task_columns(limit: Limit = 100) -> dict[str, Any]:
        return call(lambda: api.task_columns(limit=limit))

    @read(
        "juridiq_list_tasks",
        "List Juridiq tasks",
        "List tasks (paged). To dedupe a publication, filter by lawSuitId and compare the obligation, not just the title. Read-only.",
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
        options = _arguments()
        return call(lambda: api.tasks(**options))

    @read(
        "juridiq_list_tasks_kanban",
        "List Juridiq Kanban tasks",
        "List tasks grouped by Kanban status, for board questions; dedupe with juridiq_list_tasks. Read-only.",
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
        options = _arguments()
        return call(lambda: api.kanban_tasks(**options))

    @read("juridiq_get_task", "Get Juridiq task", "Get a task by ID. Read-only.")
    def get_task(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.get_task(taskId))

    @read(
        "juridiq_get_task_history",
        "Get Juridiq task history",
        "Get a task's history (actions, dates, users, Kanban moves). Read-only.",
    )
    def get_task_history(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.task_history(taskId))

    @read(
        "juridiq_list_task_comments",
        "List Juridiq task comments",
        "List a task's comments; check human notes before deciding a publication is covered. Read-only.",
    )
    def list_task_comments(taskId: Text) -> dict[str, Any]:
        return call(lambda: api.task_comments(taskId))

    @write(
        "juridiq_create_task",
        "Create Juridiq task",
        "Create a task. Required: title, priority, columnId (UUID from juridiq_list_task_columns), initialDate. Use responsiblesId, not responsibleIds. Pass lawSuitId (juridiq_find_lawsuit) so the task shows in its process; else it is standalone. If Juridiq doesn't confirm (timeout, connection error, 5xx) the task MAY have been created: check juridiq_list_tasks before retrying. Needs JURIDIQ_ALLOW_WRITES=true.",
        WRITE,
    )
    def create_task(payload: TaskCreate) -> dict[str, Any]:
        return call(lambda: api.create_task(_payload(payload)))

    @write(
        "juridiq_update_task",
        "Update Juridiq task",
        "Update a task; only supplied fields are sent. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_task(taskId: Id, patch: TaskUpdate) -> dict[str, Any]:
        return call(lambda: api.update_task(str(taskId), _payload(patch)))

    @write(
        "juridiq_delete_task",
        "Delete Juridiq task",
        "Delete only the given task. Preview with dryRun first. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_task(
        taskId: Id,
        dryRun: Annotated[
            bool, Field(description="true: delete nothing, return the task that would be deleted.")
        ] = False,
        expectTitle: Annotated[
            str,
            Field(
                max_length=255,
                description="Refuse unless the task title contains this (case ignored). Empty: no check.",
            ),
        ] = "",
    ) -> dict[str, Any]:
        return call(lambda: api.delete_task(str(taskId), expect_title=expectTitle, dry_run=dryRun))

    @write(
        "juridiq_create_tag",
        "Create Juridiq tag",
        "Create a tag (etiqueta); modules sets where it shows. Needs JURIDIQ_ALLOW_WRITES=true.",
        WRITE,
    )
    def create_tag(payload: TagCreate) -> dict[str, Any]:
        return call(lambda: api.create_tag(_payload(payload)))

    @write(
        "juridiq_update_tag",
        "Update Juridiq tag",
        "Rename, recolor or change the modules of a tag. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_tag(tagId: Id, patch: TagUpdate) -> dict[str, Any]:
        return call(lambda: api.update_tag(str(tagId), _payload(patch)))

    @write(
        "juridiq_delete_tag",
        "Delete Juridiq tag",
        "Delete only the given tag. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_tag(tagId: Id) -> dict[str, Any]:
        return call(lambda: api.delete_tag(str(tagId)))

    @write(
        "juridiq_create_task_template",
        "Create Juridiq task template",
        "Create a task template (modelo de tarefa). UUIDs: tags from juridiq_list_tags, responsiblesId from juridiq_list_users, columnId from juridiq_list_task_columns. Omitted: points 0, priority Baixa, daysForDue 1, daysForStart 0. Needs JURIDIQ_ALLOW_WRITES=true.",
        WRITE,
    )
    def create_task_template(payload: TaskTemplateCreate) -> dict[str, Any]:
        return call(lambda: api.create_task_template(_payload(payload)))

    @write(
        "juridiq_update_task_template",
        "Update Juridiq task template",
        "Update a task template; send only changed fields. subtasks replaces the whole list. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def update_task_template(templateId: Id, patch: TaskTemplateUpdate) -> dict[str, Any]:
        return call(lambda: api.update_task_template(str(templateId), _payload(patch)))

    @write(
        "juridiq_delete_task_template",
        "Delete Juridiq task template",
        "Delete only the given task template. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def delete_task_template(templateId: Id) -> dict[str, Any]:
        return call(lambda: api.delete_task_template(str(templateId)))

    @write(
        "juridiq_create_event",
        "Create Juridiq event",
        "Create a calendar event. Needs JURIDIQ_ALLOW_WRITES=true.",
        WRITE,
    )
    def create_event(payload: EventCreate) -> dict[str, Any]:
        return call(lambda: api.create_event(_payload(payload)))

    @write(
        "juridiq_mark_publications_read",
        "Mark Juridiq publications read",
        "Mark the given publication IDs as read. Needs JURIDIQ_ALLOW_WRITES=true.",
        DESTRUCTIVE,
    )
    def mark_publications_read(
        publicationIds: Annotated[list[Text], Field(min_length=1, max_length=50)],
    ) -> dict[str, Any]:
        return call(lambda: api.mark_publications_read(publicationIds))

    return server


def main():
    load_env_file()
    serve(create_server())


if __name__ == "__main__":
    main()
