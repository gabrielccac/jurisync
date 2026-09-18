"""Build Juridiq task drafts from an agent classification and live templates.

This module is intentionally write-free.  The skill decides the semantic task
type and how many tasks are needed; this code resolves each type to a live
template and expands the template into the documented POST /task payload.
"""

from __future__ import annotations

import re
from datetime import date


class TaskDraftError(ValueError):
    """Raised when a task routing or template response is unsafe/incomplete."""


# These are semantic keys used by the skill, not Juridiq API fields.
TASK_TEMPLATE_TITLES = {
    "replica_bpc": "Réplica BPC - (Nome Cliente)",
    "aceitacao_acordo": "Manifestação Aceitação Acordo - (Nome Cliente)",
    "manifestacao": "Manifestação - (Nome Cliente)",
    "pericia_medica": "Comunicação Perícia Médica Federal - (Nome Cliente)",
    "pericia_social": "Comunicação Perícia Social - (Nome do Cliente)",
    "remarcacao_pericia": "Remarcação Perícia Judicial - (Nome Cliente) ",
    "impugnacao_laudo": "Impugnação ao Laudo - (Nome Cliente)",
    "laudo_favoravel": "Manifestação Laudo Favorável - (Nome do Cliente) ",
    "sentenca_favoravel": "Comunicação Sentença Favorável - (Nome do Cliente)",
    "acordao_favoravel": "Comunicação Acórdão Favorável - (Nome do Cliente)",
    "rpv": "Expedição RPV - (Nome Cliente)",
    "recurso_inominado": "Recurso Inominado BPC - (Nome Cliente)",
    "apelacao_bpc": "Apelação BPC - (Nome do Cliente)",
    "apelacao_ms": "Apelação MS - (Nome Cliente)",
    "embargos_declaracao": "Embargos de Declaração - (Nome Cliente)",
    "inicial_bpc": "Inicial BPC - (Nome Cliente)",
    "protocolar_inicial_bpc": "Protocolar Inicial BPC - (Nome Cliente)",
}


def _require(condition, message):
    if not condition:
        raise TaskDraftError(message)


def _template_records(templates):
    if isinstance(templates, dict):
        templates = templates.get("data")
    _require(isinstance(templates, list), "templates must be an array or an API response with data")
    result = {}
    for template in templates:
        _require(isinstance(template, dict), "template records must be objects")
        title = template.get("title")
        _require(isinstance(title, str) and title, "template title must be nonempty")
        _require(title not in result, f"duplicate live template title: {title}")
        result[title] = template
    return result


def _ids(template, field, nested_field):
    records = template.get(field, [])
    _require(isinstance(records, list), f"template {field} must be an array")
    ids = []
    for record in records:
        _require(isinstance(record, dict) and isinstance(record.get(nested_field), str)
                 and record[nested_field], f"template {field} must contain IDs")
        ids.append(record[nested_field])
    _require(len(ids) == len(set(ids)), f"template {field} contains duplicate IDs")
    return ids


def _render_title(template_title, client_name):
    _require(isinstance(client_name, str) and client_name.strip(), "clientName must be nonempty")
    title = template_title
    for placeholder in ("(Nome do Cliente)", "(Nome Cliente)", "(Nome cliente)"):
        title = title.replace(placeholder, client_name.strip())
    _require("(Nome" not in title, "template title has an unsupported placeholder")
    return title


def _law_suit_id(bundle):
    publication = bundle.get("publication") if isinstance(bundle, dict) else None
    _require(isinstance(publication, dict), "bundle publication is required")
    value = publication.get("lawSuitId", publication.get("lawsuitId"))
    _require(isinstance(value, str) and value, "publication lawSuitId is required")
    return value


def _existing_match(existing_tasks, law_suit_id, title, publication_id, obligation_key):
    _require(isinstance(existing_tasks, list), "existing_tasks must be an array")
    for task in existing_tasks:
        _require(isinstance(task, dict), "existing task records must be objects")
        task_law_suit = task.get("lawSuitId", task.get("lawsuitId"))
        # A title identifies a template, not a legal obligation. Only skip a
        # task when the caller supplied the same publication/obligation key.
        if task_law_suit != law_suit_id:
            continue
        if obligation_key and task.get("obligationKey") == obligation_key:
            return task.get("id")
        if task.get("publicationId") and task.get("publicationId") == publication_id:
            return task.get("id")
    return None


def build_task_drafts(bundle, routing, templates, *, existing_tasks=None):
    """Expand skill routing into draft-only POST /task payloads.

    ``routing`` is the agent's semantic result::

        {"publicationId": "...", "status": "sufficient", "tasks": [
          {"type": "replica_bpc", "clientName": "...",
           "description": "...", "initialDate": "2026-09-08",
           "finalDate": "2026-09-15", "personIds": ["..."]}
        ]}

    Multiple task entries are intentional: a favorable sentence or agreement
    can produce both communication and RPV tasks under the skill.
    """
    _require(isinstance(bundle, dict), "bundle must be an object")
    _require(isinstance(routing, dict), "routing must be an object")
    _require(isinstance(routing.get("publicationId"), str), "routing publicationId must be a string")
    _require(routing["publicationId"] == bundle.get("publicationId"), "routing publicationId does not match bundle")
    status = routing.get("status")
    _require(status in {"sufficient", "no_action"}, "only sufficient or no_action can produce task drafts")
    task_inputs = routing.get("tasks")
    _require(isinstance(task_inputs, list), "routing tasks must be an array")
    if status == "no_action":
        _require(not task_inputs, "no_action cannot contain tasks")
    else:
        _require(task_inputs, "sufficient routing must contain at least one task")
        evidence = routing.get("evidence")
        _require(isinstance(evidence, list) and evidence, "sufficient routing must include evidence")
    if status == "no_action":
        return {
            "publicationId": routing["publicationId"],
            "status": "no_action_or_existing",
            "writePerformed": False,
            "drafts": [],
            "skippedExisting": [],
        }
    known_templates = _template_records(templates)
    law_suit_id = _law_suit_id(bundle)
    existing_tasks = [] if existing_tasks is None else existing_tasks
    drafts = []
    skipped = []
    seen_types = set()
    for task_input in task_inputs:
        _require(isinstance(task_input, dict), "task routing entries must be objects")
        task_type = task_input.get("type")
        _require(task_type in TASK_TEMPLATE_TITLES, f"unsupported publication task type: {task_type}")
        _require(task_type not in seen_types, f"duplicate task type in routing: {task_type}")
        seen_types.add(task_type)
        expected_title = TASK_TEMPLATE_TITLES[task_type]
        template = known_templates.get(expected_title)
        _require(template is not None, f"live template not found: {expected_title}")
        template_id = template.get("id")
        _require(isinstance(template_id, str) and template_id, "template id must be nonempty")
        column_id = template.get("columnId")
        priority = template.get("priority")
        office_id = template.get("officeId")
        _require(all(isinstance(value, str) and value for value in (column_id, priority, office_id)),
                 "template columnId, priority and officeId are required")
        description = task_input.get("description")
        initial_date = task_input.get("initialDate")
        _require(isinstance(description, str) and description.strip(), "task description must be nonempty")
        _require(isinstance(initial_date, str) and initial_date.strip(), "task initialDate must be explicit")
        try:
            date.fromisoformat(initial_date.strip())
        except ValueError as exc:
            raise TaskDraftError("task initialDate must be an ISO date (YYYY-MM-DD)") from exc
        person_ids = task_input.get("personIds", [])
        _require(isinstance(person_ids, list) and all(isinstance(value, str) and value for value in person_ids),
                 "personIds must be an array of nonempty strings")
        _require(len(person_ids) == len(set(person_ids)), "personIds must be unique")
        title = _render_title(expected_title, task_input.get("clientName"))
        obligation_key = task_input.get("obligationKey")
        _require(isinstance(obligation_key, str) and obligation_key.strip(),
                 "each task requires an obligationKey for duplicate protection")
        existing_id = _existing_match(existing_tasks, law_suit_id, title,
                                       routing["publicationId"], obligation_key)
        if existing_id:
            skipped.append({"taskType": task_type, "title": title, "existingTaskId": existing_id,
                            "reason": "equivalent task already exists for lawSuitId"})
            continue
        payload = {
            "title": title,
            "priority": priority,
            "columnId": column_id,
            "initialDate": initial_date,
            "description": description,
            "lawSuitId": law_suit_id,
            "responsiblesId": _ids(template, "responsibles", "id"),
            "officeId": office_id,
            "tags": _ids(template, "tags", "id"),
            "personIds": person_ids,
        }
        for field in ("finalDate", "initialHour", "finalHour"):
            if field in task_input:
                _require(isinstance(task_input[field], str) and task_input[field].strip(),
                         f"{field} must be a nonempty string when supplied")
                if field in {"finalDate"}:
                    try:
                        date.fromisoformat(task_input[field].strip())
                    except ValueError as exc:
                        raise TaskDraftError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc
                payload[field] = task_input[field]
        for field in ("points", "estimatedTimeMinutes", "createSheet"):
            if field in template:
                payload[field] = template[field]
        drafts.append({
            "action": "create",
            "taskType": task_type,
            "obligationKey": obligation_key,
            "templateId": template_id,
            "templateTitle": expected_title,
            "payload": payload,
            "templateDefaults": {
                "daysForDue": template.get("daysForDue"),
                "daysForStart": template.get("daysForStart"),
                "isBusinessDay": template.get("isBusinessDay"),
            },
        })
    return {
        "publicationId": routing["publicationId"],
        "status": "draft_only" if drafts else "no_action_or_existing",
        "writePerformed": False,
        "drafts": drafts,
        "skippedExisting": skipped,
    }
