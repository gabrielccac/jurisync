"""The lawsuit stage (Etapa) of ``AdvboxClient``: read the lawsuit form and move it to another stage."""

import re
import time
from urllib.parse import quote

from curl_cffi.requests.exceptions import RequestException

from jurisync.connectors.advbox.parsers import (
    build_lawsuit_stage_body,
    parse_lawsuit_form,
    parse_sub_stages,
)
from jurisync.connectors.advbox.session import require_writes
from jurisync.connectors.advbox.writes import UnconfirmedWriteError, _plain

# ADVBOX answers 413 to a burst of opens of one lawsuit page: wait and read again.
LAWSUIT_READ_TRIES = 4


LAWSUIT_READ_WAIT = 20


class StagesMixin:
    """Lawsuit stage reads and the guarded stage change."""

    def lawsuit_form(self, lawsuit_id):
        """The lawsuit's edit form (``GET /lawsuits/<id>/edit``) as the screen would save it (read-only).

        ADVBOX answers 413 when one lawsuit page is opened many times in a short burst (seen live after about eight
        opens in a few seconds); the read waits and tries again a few times, so the check after a save is not lost."""
        path = f"/lawsuits/{quote(str(lawsuit_id), safe='')}/edit"
        for attempt in range(LAWSUIT_READ_TRIES):
            try:
                html = self.session.using(lambda s: s.get_html(path))
                break
            except RuntimeError as error:
                if "(413)" not in str(error):
                    raise
                if attempt + 1 == LAWSUIT_READ_TRIES:
                    raise RuntimeError(
                        f"ADVBOX refused to open lawsuit {lawsuit_id} again (413, too many opens in a short time); "
                        "wait a minute and try again"
                    ) from error
                time.sleep(LAWSUIT_READ_WAIT)
        form = parse_lawsuit_form(html)
        if form["lawsuitId"] != str(lawsuit_id):
            raise ValueError(f"ADVBOX returned the form of lawsuit {form['lawsuitId']}, not {lawsuit_id}")
        return form

    def sub_stages(self, stage_id):
        """The sub-stages (Etapas) of one stage, as the screen loads them when the stage changes (read-only)."""
        return parse_sub_stages(self.session.post(f"/stages/{quote(str(stage_id), safe='')}", {}))

    @staticmethod
    def _stage_state(form, stage_id, sub_stage_id, sub_options=None):
        stages = {option["id"]: option["label"] for option in form["stages"]}
        labels = {option["id"]: option["label"] for option in (sub_options or form["subStages"])}
        return {
            "stageId": stage_id,
            "stage": stages.get(stage_id, ""),
            "subStageId": sub_stage_id,
            "subStage": labels.get(sub_stage_id, ""),
        }

    def _stage_plan(self, lawsuit_id, stage_id, sub_stage_id=""):
        """Check a stage change against the lawsuit's own form; ``(form, target sub-stage, plan)``."""
        form = self.lawsuit_form(lawsuit_id)
        stage_id, sub_stage_id = str(stage_id).strip(), str(sub_stage_id or "").strip()
        known = {option["id"]: option["label"] for option in form["stages"]}
        if stage_id not in known:
            options = ", ".join(f"{key} ({label})" for key, label in known.items())
            raise ValueError(f"Unknown ADVBOX stage id {stage_id!r}; the lawsuit form offers: {options}")
        options = self.sub_stages(stage_id)
        ids = [option["id"] for option in options]
        defaulted = False
        if sub_stage_id:
            if sub_stage_id not in ids:
                listed = ", ".join(f"{option['id']} ({option['label']})" for option in options) or "none"
                raise ValueError(
                    f"Unknown sub-stage id {sub_stage_id!r} for stage {stage_id} ({known[stage_id]}); options: {listed}"
                )
        elif stage_id == form["stageId"]:
            sub_stage_id = form["subStageId"]  # same stage: keep the current sub-stage
        else:
            sub_stage_id, defaulted = (ids[0] if ids else ""), True  # what the screen selects after a stage change
        target = (stage_id, sub_stage_id)
        new = self._stage_state(form, stage_id, sub_stage_id, options)
        plan = {
            "lawsuitId": str(lawsuit_id),
            "changed": target != (form["stageId"], form["subStageId"]),
            "previous": self._stage_state(form, form["stageId"], form["subStageId"]),
            "new": new,
            "subStageDefaulted": defaulted,
            "subStageOptions": options,
            "warnings": [],
        }
        workflow = next((option["workflowId"] for option in options if option["id"] == sub_stage_id), None)
        if workflow and plan["changed"]:
            plan["warnings"].append(
                f"Sub-stage {sub_stage_id} ({new['subStage']}) is linked to ADVBOX workflow {workflow}; the screen may "
                "offer to start its tasks. This tool never starts it."
            )
        return form, sub_stage_id, plan

    def update_lawsuit_stage(self, lawsuit_id, stage_id, sub_stage_id="", *, dry_run=False):
        """Move a lawsuit to ``stage_id`` (``steps_id``, the Tarefas filter's stage) and ``sub_stage_id``
        (``stages_id``) the way the lawsuit screen does: its whole edit form is sent back with only those two fields
        changed. Without ``sub_stage_id`` a new stage gets its first sub-stage (as the screen does) and the same
        stage keeps the current one. Unknown ids are refused before anything is sent. The result is confirmed by
        reading the form again; ``previous`` is what to pass to undo the change."""
        if not dry_run:
            require_writes()
        with self._exclusive():
            form, sub_stage_id, plan = self._stage_plan(lawsuit_id, stage_id, sub_stage_id)
            body = build_lawsuit_stage_body(form, plan["new"]["stageId"], sub_stage_id)
            if dry_run:
                return {
                    "dryRun": True,
                    **plan,
                    "request": {
                        "method": "POST",
                        "path": f"/lawsuits/{lawsuit_id}",
                        "_method": "PUT",
                        "fields": len(body),
                    },
                }
            if not plan["changed"]:
                return {**plan, "confirmedBy": "already at this stage; nothing was sent"}
            cause = None
            try:
                response = self.session.post(f"/lawsuits/{quote(str(lawsuit_id), safe='')}", body)
            except RequestException as error:
                cause, response = error, None
            except RuntimeError as error:
                if not re.search(r"failed \(5\d\d\)", str(error)):
                    raise RuntimeError(f"{_plain(error)}. The lawsuit stage was not changed.") from error
                cause, response = error, None
            try:
                after = self.lawsuit_form(lawsuit_id)
            except Exception as error:  # noqa: BLE001 - the outcome is unknown: say so
                raise UnconfirmedWriteError(
                    f"ADVBOX {'did not answer' if cause else 'answered'}, but the lawsuit form could not be read "
                    f"again ({_plain(error)}): the stage change of lawsuit {lawsuit_id} is NOT confirmed. Read the "
                    "lawsuit before retrying."
                ) from error
        reached = (after["stageId"], after["subStageId"]) == (plan["new"]["stageId"], sub_stage_id)
        if not reached:
            now = self._stage_state(after, after["stageId"], after["subStageId"])
            raise UnconfirmedWriteError(
                f"The lawsuit form shows stage {now['stageId']} ({now['stage']}) / sub-stage {now['subStageId']} "
                f"({now['subStage']}) after the save, not the requested one: the change is NOT confirmed."
                + (f" Cause: {_plain(cause) or type(cause).__name__}." if cause else "")
            )
        changed = self._other_fields_changed(form, after)
        if changed:
            plan["warnings"].append(
                f"Other lawsuit fields read back differently after the save: {', '.join(changed)}. Check the lawsuit."
            )
        message = ""
        if isinstance(response, dict):
            message = re.sub(r"<[^>]+>", "", str(response.get("message") or "")).strip()
        if not plan["subStageDefaulted"]:
            plan.pop("subStageOptions")  # only worth returning when the sub-stage was chosen for the caller
        return {**plan, "message": message, "confirmedBy": "lawsuit form read again"}

    @staticmethod
    def _other_fields_changed(before, after):
        ignored = {"_token", "steps_id", "stages_id"}

        def fields(form):
            grouped = {}
            for name, value in form["pairs"]:
                if name not in ignored:
                    grouped.setdefault(name, []).append(value)
            return grouped

        old, new = fields(before), fields(after)
        return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
