"""Agent-orchestrated Juridiq publication-to-task pipeline.

The caller supplies the agent (legal judgment), an explicit customer profile
(task-template mapping), and an absolute state path. This module owns the
mechanics: polling, deduplication checks, quoted-evidence validation, live
template resolution, existing-task checks, verified writes, and read marking.

Agent methods: ``deduplicate``, ``assess`` and ``route`` are required;
``enrich`` and ``provisional_route`` only for the branches that use them.
Each receives keyword arguments and returns plain dicts.
"""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from jurisync.connectors.juridiq.api import JuridiqApi, payload_data

ASSESSMENT_STATUSES = ("sufficient", "no_action", "needs_context", "review")
READ_BATCH = 50


class PipelineError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PipelineError(message)


def _now():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _date(value, field):
    require(isinstance(value, str) and len(value) == 10, f"{field} must be YYYY-MM-DD")
    try:
        require(date.fromisoformat(value).isoformat() == value, f"{field} is invalid")
    except ValueError:
        raise PipelineError(f"{field} is invalid") from None
    return value


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def template_map(templates, profile):
    by_title = {}
    for template in templates:
        require(isinstance(template, dict) and _text(template.get("title")), "template title is required")
        require(template["title"] not in by_title, f"duplicate template title: {template['title']}")
        by_title[template["title"]] = template
    for task_type in profile.get("requiredTaskTypes") or list(profile["taskTemplates"]):
        title = profile["taskTemplates"].get(task_type)
        require(_text(title), f"profile template title is missing: {task_type}")
        require(title in by_title, f"required skill template not found: {title}")
    return by_title


def _ids(template, field):
    values = template.get(field) or []
    require(isinstance(values, list), f"template {field} must be an array")
    ids = []
    for item in values:
        value = item if isinstance(item, str) else (item or {}).get("id")
        require(_text(value), f"template {field} contains an invalid ID")
        ids.append(value)
    return ids


def render_title(template_title, client_name):
    require(_text(client_name), "clientName is required")
    name = client_name.strip()
    return template_title.replace("(Nome do Cliente)", name).replace("(Nome Cliente)", name)


def normalize_publication(publication):
    publication = publication or {}
    text = next(
        (publication[key] for key in ("text", "content", "teor", "body") if publication.get(key) is not None), ""
    )
    return {
        **publication,
        "text": text,
        "availabilityDate": publication.get("availabilityDate") or publication.get("publicationDate"),
    }


def build_payload(publication, task, template):
    title = render_title(template["title"], task.get("clientName"))
    require(_text(task.get("obligationKey")), "obligationKey is required")
    require(_text(task.get("description")), "task description is required")
    process_number = publication.get("processNumber") or "processo não identificado"
    payload = {
        "title": title,
        "priority": template.get("priority"),
        "columnId": template.get("columnId"),
        "initialDate": _date(task.get("initialDate"), "initialDate"),
        "description": f"  Fonte: publicação {publication['id']}; processo {process_number}.\n{task['description'].strip()} ",
        "lawSuitId": task.get("lawSuitId"),
        "responsiblesId": _ids(template, "responsibles"),
        "officeId": template.get("officeId"),
        "tags": _ids(template, "tags"),
        "personIds": task.get("personIds") or [],
    }
    require(_text(payload["lawSuitId"]), "lawSuitId is required")
    require(
        isinstance(payload["personIds"], list) and all(_text(item) for item in payload["personIds"]),
        "personIds must be IDs",
    )
    if "finalDate" in task:
        payload["finalDate"] = _date(task["finalDate"], "finalDate")
    for field in ("initialHour", "finalHour"):
        if field in task:
            require(_text(task[field]), f"{field} is invalid")
            payload[field] = task[field]
    for field in ("points", "estimatedTimeMinutes", "createSheet"):
        if field in template:
            payload[field] = template[field]
    return {"title": title, "obligationKey": task["obligationKey"], "payload": payload}


def _task_lawsuit(task):
    return task.get("lawSuitId") or (task.get("lawSuit") or {}).get("id") or task.get("lawsuitId")


def existing_task_id(existing, lawsuit_id, publication_id, obligation_key, explicit_ids=()):
    for task in existing:
        if _task_lawsuit(task) != lawsuit_id:
            continue
        if (
            task.get("id") in explicit_ids
            or task.get("publicationId") == publication_id
            or task.get("obligationKey") == obligation_key
        ):
            return task.get("id")
    return None


def source_map(publication, context):
    normalized = normalize_publication(publication)
    require(_text(normalized.get("id")), "publication id is required for evidence")
    require(isinstance(normalized["text"], str), "publication text must be a string")
    sources = {f"publication:{normalized['id']}": normalized["text"]}
    if context is not None:
        require(isinstance(context, dict), "enrichment context must be an object")
        require(isinstance(context.get("sources"), list), "enrichment context sources must be an array")
        for source in context["sources"]:
            require(isinstance(source, dict) and _text(source.get("sourceId")), "enrichment sourceId is required")
            require(isinstance(source.get("text"), str), "enrichment source text must be a string")
            if source["sourceId"] in sources:
                require(
                    sources[source["sourceId"]] == source["text"],
                    f"duplicate evidence source differs: {source['sourceId']}",
                )
            else:
                sources[source["sourceId"]] = source["text"]
    return sources


def check_evidence(evidence, sources, label):
    require(isinstance(evidence, list), f"{label} evidence must be an array")
    for item in evidence:
        require(isinstance(item, dict) and _text(item.get("sourceId")), f"{label} evidence sourceId is required")
        require(_text(item.get("quote")), f"{label} evidence quote is required")
        require(item["sourceId"] in sources, f"{label} evidence sourceId is unknown")
        require(item["quote"] in sources[item["sourceId"]], f"{label} evidence quote is not present in claimed source")


def check_assessment(assessment, publication_id, publication, context):
    require(
        isinstance(assessment, dict) and assessment.get("publicationId") == publication_id,
        "assessment publicationId does not match",
    )
    require(assessment.get("status") in ASSESSMENT_STATUSES, "invalid assessment status")
    require(_text(assessment.get("reason")), "assessment reason is required")
    require(
        isinstance(assessment.get("evidence"), list) and isinstance(assessment.get("missingInformation"), list),
        "assessment evidence/missingInformation must be arrays",
    )
    check_evidence(assessment["evidence"], source_map(publication, context), "assessment")
    if assessment["status"] in ("sufficient", "no_action"):
        require(not assessment["missingInformation"], f"{assessment['status']} cannot have missing information")
        require(assessment["evidence"], f"{assessment['status']} requires evidence")
    return assessment


def check_deduplication(decision, publications):
    require(isinstance(decision, dict) and isinstance(decision.get("groups"), list), "deduplicate must return groups")
    known = {publication["id"] for publication in publications}
    seen, groups = set(), []
    for group in decision["groups"]:
        ids = group.get("publicationIds") if isinstance(group, dict) else None
        require(isinstance(ids, list) and ids, "dedup group publicationIds are required")
        require(
            isinstance(group.get("primaryPublicationId"), str) and group["primaryPublicationId"] in ids,
            "dedup primaryPublicationId must belong to group",
        )
        require(_text(group.get("canonicalActKey")), "dedup canonicalActKey is required")
        require(_text(group.get("reason")), "dedup reason is required")
        for publication_id in ids:
            require(publication_id in known, f"dedup references unknown publication: {publication_id}")
            require(publication_id not in seen, f"publication appears in multiple dedup groups: {publication_id}")
            seen.add(publication_id)
        duplicates = group.get("duplicatePublicationIds")
        if duplicates is None:
            duplicates = [item for item in ids if item != group["primaryPublicationId"]]
        require(isinstance(duplicates, list), "dedup duplicatePublicationIds must be an array")
        require(len(set(duplicates)) == len(duplicates), "dedup duplicatePublicationIds contains duplicates")
        require(
            all(item in ids and item != group["primaryPublicationId"] for item in duplicates),
            "dedup duplicate ID is invalid",
        )
        groups.append({**group, "duplicatePublicationIds": duplicates, "status": group.get("status") or "process"})
    require(len(seen) == len(publications), "dedup must account for every fetched publication")
    return groups


def check_routing(route, publication_id, publication, context):
    require(
        isinstance(route, dict) and route.get("publicationId") == publication_id, "routing publicationId does not match"
    )
    require(route.get("status") in ("sufficient", "no_action"), "routing must be sufficient or no_action")
    require(isinstance(route.get("tasks"), list), "routing tasks must be an array")
    require(
        not route["tasks"] if route["status"] == "no_action" else bool(route["tasks"]),
        "routing task count conflicts with status",
    )
    require(isinstance(route.get("events", []), list), "routing events must be an array")
    if route["status"] == "sufficient":
        require(isinstance(route.get("evidence"), list) and route["evidence"], "routing requires evidence")
        require(_text(route.get("classificationRule")), "routing must name the skill classification rule")
        check_evidence(route["evidence"], source_map(publication, context), "routing")
    keys = set()
    for task in route["tasks"]:
        require(isinstance(task, dict) and _text(task.get("obligationKey")), "each task requires an obligationKey")
        require(task["obligationKey"] not in keys, f"duplicate obligationKey: {task['obligationKey']}")
        keys.add(task["obligationKey"])
        require(task.get("action", "create") in ("create", "update"), "task action must be create or update")
    return route


def check_event(event, publication, context, lawsuit_id):
    require(isinstance(event, dict) and _text(event.get("eventKey")), "eventKey is required")
    require(
        event.get("action", "create") in ("create", "skip_existing"), "event action must be create or skip_existing"
    )
    require(_text(event.get("reason")), "event reason is required")
    check_evidence(event.get("evidence"), source_map(publication, context), "event")
    if event.get("action") == "skip_existing":
        return event
    payload = event.get("payload")
    require(isinstance(payload, dict), "event payload is required")
    require(_text(payload.get("title")), "event title is required")
    require(isinstance(payload.get("date"), str), "event date is required")
    _date(payload["date"], "event.date")
    for field in ("initialHour", "finalHour"):
        value = payload.get(field)
        require(
            isinstance(value, str) and len(value) == 5 and value[2] == ":" and value.replace(":", "").isdigit(),
            f"event {field} must be HH:MM",
        )
    require(isinstance(payload.get("responsibleIds"), list), "event responsibleIds must be an array")
    require(all(_text(item) for item in payload["responsibleIds"]), "event responsibleIds must contain IDs")
    if "lawSuitId" in payload:
        require(payload["lawSuitId"] == lawsuit_id, "event lawSuitId does not match")
    return event


def _id(value):
    return (value or {}).get("id") or ((value or {}).get("data") or {}).get("id")


class _State:
    def __init__(self, path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.data = {"publications": {}, "events": {}}
        self.data.setdefault("events", {})

    @property
    def publications(self):
        return self.data["publications"]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)


class PublicationPipeline:
    def __init__(self, *, agent, profile, state_file, api=None, limit=50, max_context_rounds=1):
        state_file = Path(state_file) if state_file is not None else None
        require(
            state_file is not None and state_file.is_absolute(), "stateFile must be an absolute customer-specific path"
        )
        require(
            all(callable(getattr(agent, name, None)) for name in ("assess", "route", "deduplicate")),
            "agent.assess, agent.deduplicate and agent.route are required",
        )
        require(
            isinstance(profile, dict) and _text(profile.get("id")) and isinstance(profile.get("taskTemplates"), dict),
            "an explicit profile with id and taskTemplates is required",
        )
        self.agent, self.profile, self.api = agent, profile, api or JuridiqApi()
        self.state = _State(state_file)
        self.limit, self.max_context_rounds = limit, max_context_rounds
        self.task_cache = {}
        self.pending_read = {}
        self.result = {
            key: []
            for key in (
                "reviewed",
                "noAction",
                "created",
                "updated",
                "existing",
                "duplicates",
                "eventsCreated",
                "eventsSkipped",
                "readMarked",
                "readPending",
                "errors",
            )
        }

    def _existing(self, lawsuit_id):
        if lawsuit_id not in self.task_cache:
            self.task_cache[lawsuit_id] = payload_data(self.api.tasks(lawSuitId=lawsuit_id))
        return self.task_cache[lawsuit_id]

    def _mark(self, publication_ids, **fields):
        for publication_id in publication_ids:
            self.state.publications[publication_id] = {**fields, "updatedAt": _now()}

    def _execute_route(self, route, publication, lawsuit, context, templates, publication_id):
        existing, handled = self._existing(lawsuit["id"]), []
        for task in route["tasks"]:
            expected_title = self.profile["taskTemplates"].get(task.get("type"))
            require(expected_title, f"unsupported task type: {task.get('type')}")
            template = templates.get(expected_title)
            require(template, f"live task template not found: {expected_title}")
            draft = build_payload(publication, {**task, "lawSuitId": lawsuit["id"]}, template)
            explicit = task.get("existingTaskId")
            if explicit:
                require(
                    any(item.get("id") == explicit and _task_lawsuit(item) == lawsuit["id"] for item in existing),
                    f"existing task is not loaded for lawsuit: {explicit}",
                )
            existing_id = explicit or existing_task_id(
                existing, lawsuit["id"], publication_id, draft["obligationKey"], route.get("existingTaskIds") or []
            )
            entry = {"publicationId": publication_id, "obligationKey": draft["obligationKey"]}
            if task.get("action", "create") == "update":
                require(
                    existing_id, f"update requested but no existing task was identified for {draft['obligationKey']}"
                )
                self.api.update_task(existing_id, {k: v for k, v in draft["payload"].items() if k != "officeId"})
                verified = self.api.get_task(existing_id)
                require(_id(verified) == existing_id, "updated task could not be verified")
                self.result["updated"].append({**entry, "taskId": existing_id})
                handled.append(existing_id)
                cached = {**verified, **draft["payload"], "id": existing_id, "lawSuitId": lawsuit["id"], **entry}
                index = next((i for i, item in enumerate(existing) if item.get("id") == existing_id), None)
                if index is None:
                    existing.append(cached)
                else:
                    existing[index] = cached
                continue
            if existing_id:
                self.result["existing"].append({**entry, "taskId": existing_id})
                handled.append(existing_id)
                continue
            created_id = _id(self.api.create_task(draft["payload"]))
            require(_text(created_id), "create_task did not return a task ID")
            verified = self.api.get_task(created_id)
            require(_id(verified) == created_id, "created task could not be verified")
            self.result["created"].append({**entry, "taskId": created_id})
            handled.append(created_id)
            existing.append({**verified, **draft["payload"], "id": created_id, "lawSuitId": lawsuit["id"], **entry})
        return handled

    def _finish_no_action(self, group, primary_id):
        self.result["noAction"].append(primary_id)
        self._finish(group, primary_id, "no_action")

    def _finish(self, group, primary_id, outcome):
        for duplicate_id in group["duplicatePublicationIds"]:
            self.result["duplicates"].append({"publicationId": duplicate_id, "primaryPublicationId": primary_id})
        for publication_id in group["publicationIds"]:
            self.pending_read[publication_id] = True
            self.state.publications[publication_id] = {
                **self.state.publications.get(publication_id, {}),
                "outcome": outcome,
                "primaryPublicationId": primary_id,
                "status": "pending_read",
                "updatedAt": _now(),
            }

    def _review(self, ids, status, reason):
        self._mark(ids, status="review", reason=reason)
        self.result["reviewed"] += [{"publicationId": item, "status": status, "reason": reason} for item in ids]
        self.state.save()

    def _process(self, group, publication, templates, event_keys):
        primary_id = group["primaryPublicationId"]
        lawsuit = (
            {"id": publication["lawSuitId"], "processNumber": publication.get("processNumber")}
            if publication.get("lawSuitId")
            else None
        )
        if not lawsuit and publication.get("processNumber"):
            lawsuit = self.api.find_exact_lawsuit(publication["processNumber"])
        context, provisional_ids = None, []
        assessment = check_assessment(
            self.agent.assess(publication=publication, lawSuit=lawsuit, context=context, profile=self.profile),
            primary_id,
            publication,
            context,
        )
        if assessment["status"] == "needs_context" and assessment.get("provisional") is True:
            require((lawsuit or {}).get("id"), "a provisional task requires an exact Juridiq lawSuit")
            require(
                callable(getattr(self.agent, "provisional_route", None)),
                "agent.provisional_route is required when assessment.provisional is true",
            )
            route = check_routing(
                self.agent.provisional_route(
                    publication=publication,
                    assessment=assessment,
                    lawSuit=lawsuit,
                    existingTasks=self._existing(lawsuit["id"]),
                    templates=list(templates.values()),
                    profile=self.profile,
                ),
                primary_id,
                publication,
                context,
            )
            provisional_ids = self._execute_route(route, publication, lawsuit, context, templates, primary_id)
        rounds = 0
        while assessment["status"] == "needs_context" and rounds < self.max_context_rounds:
            require(callable(getattr(self.agent, "enrich", None)), "agent.enrich is required for needs_context")
            context = self.agent.enrich(
                publication=publication,
                lawSuit=lawsuit,
                assessment=assessment,
                context=context,
                provisionalTaskIds=provisional_ids,
                profile=self.profile,
            )
            assessment = check_assessment(
                self.agent.assess(
                    publication=publication,
                    lawSuit=lawsuit,
                    assessment=assessment,
                    context=context,
                    profile=self.profile,
                ),
                primary_id,
                publication,
                context,
            )
            rounds += 1
        if assessment["status"] in ("needs_context", "review"):
            self._review(group["publicationIds"], assessment["status"], assessment["reason"])
            return
        if assessment["status"] == "no_action":
            self._finish_no_action(group, primary_id)
            return
        require((lawsuit or {}).get("id"), "exact Juridiq lawSuit was not found")
        route = check_routing(
            self.agent.route(
                publication=publication,
                assessment=assessment,
                context=context,
                lawSuit=lawsuit,
                existingTasks=self._existing(lawsuit["id"]),
                provisionalTaskIds=provisional_ids,
                templates=list(templates.values()),
                profile=self.profile,
            ),
            primary_id,
            publication,
            context,
        )
        if route["status"] == "no_action":
            self._finish_no_action(group, primary_id)
            return
        if provisional_ids:
            require(
                any(
                    task.get("action") == "update" and task.get("existingTaskId") in provisional_ids
                    for task in route["tasks"]
                ),
                "a provisional task must be updated in place after enrichment",
            )
        self._execute_route(route, publication, lawsuit, context, templates, primary_id)
        for event in route.get("events") or []:
            check_event(event, publication, context, lawsuit["id"])
            if event.get("action", "create") == "skip_existing" or event["eventKey"] in event_keys:
                self.result["eventsSkipped"].append(
                    {"publicationId": primary_id, "eventKey": event["eventKey"], "reason": event["reason"]}
                )
                continue
            event_id = _id(self.api.create_event(event["payload"]))
            event_keys.add(event["eventKey"])
            self.state.data["events"][event["eventKey"]] = {
                "status": "completed",
                "eventKey": event["eventKey"],
                "eventId": event_id,
                "publicationId": primary_id,
                "updatedAt": _now(),
            }
            self.result["eventsCreated"].append(
                {
                    "publicationId": primary_id,
                    "eventKey": event["eventKey"],
                    "eventId": event_id,
                    "verification": "indirect_only",
                }
            )
        self._finish(group, primary_id, "processed")

    def _mark_read(self):
        ids = list(self.pending_read)
        for start in range(0, len(ids), READ_BATCH):
            batch = ids[start : start + READ_BATCH]
            try:
                self.api.mark_publications_read(batch)
            except Exception as error:  # Record and continue: unread publications are retried next run.
                self.result["readPending"] += [{"publicationId": item, "reason": str(error)} for item in batch]
                self.result["errors"].append({"publicationIds": batch, "stage": "mark_read", "error": str(error)})
            else:
                for item in batch:
                    self.state.publications[item] = {
                        **self.state.publications[item],
                        "status": "completed",
                        "markedRead": True,
                        "updatedAt": _now(),
                    }
                    self.result["readMarked"].append(item)
            self.state.save()

    def run(self):
        publications = [normalize_publication(item) for item in self.api.all_unread_publications(maxResults=self.limit)]
        result = {"fetched": len(publications), "skipped": 0, **self.result}
        self.result = result
        if not publications:
            return result
        templates = template_map(self.api.task_templates(limit=100), self.profile)
        groups = check_deduplication(
            self.agent.deduplicate(publications=publications, profile=self.profile), publications
        )
        by_id = {publication["id"]: publication for publication in publications}
        event_keys = {
            item["eventKey"]
            for item in self.state.data["events"].values()
            if isinstance(item, dict) and item.get("status") == "completed"
        }
        for group in groups:
            ids, primary_id = group["publicationIds"], group["primaryPublicationId"]
            if group["status"] == "review":
                self._review(ids, "review", group["reason"])
                continue
            if all(
                self.state.publications.get(item, {}).get("status") == "completed"
                and self.state.publications[item].get("markedRead") is True
                for item in ids
            ):
                result["skipped"] += len(ids)
                continue
            try:
                self._process(group, by_id[primary_id], templates, event_keys)
            except Exception as error:  # One publication's failure must not stop the batch.
                self._mark([primary_id], status="error", error=str(error))
                for duplicate_id in group["duplicatePublicationIds"]:
                    self._mark(
                        [duplicate_id],
                        status="error",
                        error=f"primary publication failed: {error}",
                        primaryPublicationId=primary_id,
                    )
                result["errors"].append({"publicationId": primary_id, "error": str(error)})
            self.state.save()
        self._mark_read()
        return result


def run_publication_pipeline(*, agent, profile, state_file, api=None, limit=50, max_context_rounds=1):
    return PublicationPipeline(
        agent=agent, profile=profile, state_file=state_file, api=api, limit=limit, max_context_rounds=max_context_rounds
    ).run()
