"""Read-only ADVBOX operations of ``AdvboxClient``: customers, lawsuits, members, workflows, tasks, intimações, reports."""

from urllib.parse import quote, urlencode

from jurisync.connectors.advbox.parsers import (
    normalize_activities,
    normalize_publications,
    normalize_table,
    parse_activity_details,
    parse_lawsuit_report,
    parse_members,
    parse_publication_courts,
    parse_task_filters,
    parse_workflow_steps,
    parse_workflows,
)
from jurisync.connectors.advbox.queries import (
    PRIORITIES,
    TABLES,
    build_activities_body,
    build_publications_query,
    publication_dates,
    resolve_court,
    table_path,
)


def _compact_task(record, detail_chars):
    detail = record.get("detail") or ""
    flags = {key: record[key] for key in ("important", "urgent", "viewed") if record.get(key) is not None}
    return {
        "postId": record["postId"],
        "task": record["task"],
        "detail": detail[:detail_chars] + ("…" if len(detail) > detail_chars else ""),
        "customer": record.get("customer"),
        "lawsuitId": record.get("lawsuitId"),
        "taskTypeId": record.get("taskTemplateId"),
        "start": record.get("start"),
        "deadline": record.get("deadline") or None,
        "overdue": record.get("overdue"),
        "responsible": [person["name"] for person in record.get("responsible") or []],
        **flags,
    }


class ReadsMixin:
    """Listings, reports and workflows; nothing here writes."""

    def list_customers(self, **options):
        return self.session.using(
            lambda s: normalize_table(s.get_json(table_path("customers", **options)), TABLES["customers"]["names"])
        )

    def list_lawsuits(self, **options):
        return self.session.using(
            lambda s: normalize_table(s.get_json(table_path("lawsuits", **options)), TABLES["lawsuits"]["names"])
        )

    def list_members(self):
        return self.session.using(lambda s: parse_members(s.get_html("/posts/create")))

    def list_workflows(self):
        return self.session.using(lambda s: parse_workflows(s.get_html("/posts/create")))

    def list_lawsuit_partners(self, lawsuit_id):
        payload = self.session.post("/lawsuits/network", {"id": str(lawsuit_id)})
        return [
            {"id": int(entry["id"]), "name": str(entry.get("name") or "")}
            for entry in (payload if isinstance(payload, list) else [])
        ]

    def workflow_steps(self, workflow_id):
        steps, title, total, sequence = [], "", 0, 1
        while True:
            page = parse_workflow_steps(
                self.session.post("/workflow/tasks", {"id": str(workflow_id), "sequence": str(sequence)})
            )
            title = title or page["title"]
            total = page["total"] or sequence
            steps += page["steps"]
            if sequence >= total:
                break
            if sequence > 20:
                raise RuntimeError(f"ADVBOX workflow {workflow_id} exceeded 20 steps")
            sequence += 1
        return {"workflowId": int(workflow_id), "title": title, "totalSteps": total, "steps": steps[:total]}

    def list_activities(self, *, pageSize=50, **options):
        """One page of the Tarefas listing.

        Without ``responsibleIds`` the server lists only the logged-in user's tasks. The server refuses (HTTP 413)
        a filter that matches several thousand tasks, such as every member's pending tasks or completed tasks
        without a date range; that is reported as a ValueError asking for narrower filters.
        """

        def read(session):
            try:
                payload = session.with_csrf(
                    lambda token: session.post_content(
                        "/content/posts", build_activities_body(token, pageSize=pageSize, **options), retry_429=True
                    )
                )
            except RuntimeError as error:
                if "(413)" not in str(error):
                    raise
                raise ValueError(
                    "ADVBOX refused this task listing because it matches too many tasks (HTTP 413). Narrow it with "
                    "fewer responsibles, a date range (required for completed tasks), a search text, a task type, a "
                    "stage, a group or a lawsuit type."
                ) from None
            return normalize_activities(payload, pageSize)

        return self.session.using(read)

    def task_filters(self):
        """Options of the Tarefas filters: task types, stages, groups, lawsuit types, situations and priorities."""
        if getattr(self, "_task_filters", None) is None:
            self._task_filters = parse_task_filters(self.session.using(lambda s: s.get_html("/content/home")))
        return self._task_filters

    def search_tasks(
        self,
        search="",
        *,
        completed="pendentes",
        responsible=None,
        allResponsibles=None,
        dateFrom="",
        dateTo="",
        taskType="",
        stage="",
        groups=(),
        lawsuitTypes=(),
        priorities=(),
        page=1,
        pageSize=25,
        detailChars=400,
    ):
        """Tasks matching a free text and/or the Tarefas filters, as compact records.

        ``search`` matches the title, comment, customer and process number. Without ``responsible`` a text search
        covers every member and a listing without text only the logged-in user (ADVBOX's own default);
        ``allResponsibles`` overrides that. Completed tasks need a date range: ADVBOX refuses them otherwise.
        """
        if completed == "concluidas" and not (dateFrom and dateTo):
            raise ValueError("Completed tasks need dateFrom and dateTo; ADVBOX refuses them otherwise")
        everyone = bool(search) if allResponsibles is None else allResponsibles
        responsible_ids = list(
            responsible or ([user["id"] for user in self.list_members()["users"]] if everyone else [])
        )
        result = self.list_activities(
            page=page,
            pageSize=pageSize,
            search=search,
            completed=completed,
            responsibleIds=responsible_ids,
            dateFrom=dateFrom,
            dateTo=dateTo,
            taskTypeId=taskType,
            stepId=stage,
            groupIds=list(groups or ()),
            lawsuitTypeIds=list(lawsuitTypes or ()),
            priorities=[PRIORITIES[name] for name in priorities] if priorities else (1, 2, 3),
        )
        tasks = [_compact_task(record, detailChars) for record in result["records"]]
        return {
            "search": search,
            "scope": "responsible" if responsible else ("everyone" if everyone else "logged-in user"),
            "total": result["total"],
            "page": page,
            "pageSize": pageSize,
            "hasMore": page * pageSize < result["total"],
            "tasks": tasks,
        }

    def publication_courts(self):
        """Court filter options (id, sigla, label) read once from the Intimações page."""
        if not self._courts:
            self._courts = parse_publication_courts(self.session.using(lambda s: s.get_html("/content/publications")))
        return self._courts

    def list_publications(
        self,
        *,
        period=None,
        dateFrom="",
        dateTo="",
        tribunal="",
        processNumber="",
        responsibleId="",
        page=1,
        pageSize=50,
    ):
        """Intimações received by the office, filtered by period, court, process or responsible (read-only).

        ``period`` is a screen preset (today, 3d, 7d, 30d); otherwise both dates are required. Pages are capped
        at ``PUBLICATION_PAGE_SIZE_MAX`` records, and every record keeps the post ID that opens the intimação.
        """
        date_from, date_until = publication_dates(period, dateFrom, dateTo)
        court = resolve_court(self.publication_courts(), tribunal) if tribunal else None

        def read(session):
            query = build_publications_query(
                session.csrf_token(),
                page=page,
                pageSize=pageSize,
                dateFrom=date_from,
                dateTo=date_until,
                courtId=court["id"] if court else "",
                processNumber=processNumber,
                responsibleId=responsibleId,
            )
            return session.get_json(f"/content/publications?{query}")

        try:
            payload = self.session.using(read)
        except RuntimeError as error:
            if "failed (404)" in str(error):
                raise RuntimeError("ADVBOX Intimações are not enabled for this account") from error
            raise
        return {
            "dateFrom": date_from,
            "dateUntil": date_until,
            "tribunal": court,
            **normalize_publications(payload, pageSize, page),
        }

    def activity_details(self, post_id):
        return self.session.using(
            lambda s: parse_activity_details(s.get_html(f"/posts/{quote(str(post_id), safe='')}/edit?autoread=0"))
        )

    def lawsuit_report(self, lawsuit_id, *, movements=True, publications=True, tasks=True, transactions=True):
        def flag(value):
            return "1" if value else "0"

        query = urlencode(
            {
                "uid": str(lawsuit_id),
                "show_auto_movements": flag(movements),
                "show_manual_movements": flag(movements),
                "show_publications": flag(publications),
                "show_tasks": flag(tasks),
                "show_transactions": flag(transactions),
            }
        )
        return self.session.using(lambda s: parse_lawsuit_report(s.get_html(f"/history?{query}")))
