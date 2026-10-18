"""Fill a created draft end to end, one phase at a time.

Each phase reads the draft once, acts only on what is missing, and is recorded in the checkpoint with a fingerprint of
the payload it worked from. A re-run skips phases whose fingerprint still matches; `verify_all` ignores the record and
re-reads everything instead.
"""

from jurisync.connectors.pje.filing.checkpoint import UploadLedger, now, phase_fingerprints
from jurisync.connectors.pje.filing.documents import expected_documents
from jurisync.connectors.pje.filing.payload import payload_subjects
from jurisync.connectors.pje.filing.timeline import StepTimeline


class FillMixin:
    def fill_draft(self, checkpoint, payload, timeline=None, *, verify_all=False, save=None):
        timeline = timeline or StepTimeline(None, self, None)
        draft_url, draft_id = checkpoint["draftUrl"], checkpoint["draftId"]
        author = payload["author"]
        completed = checkpoint.setdefault("completed", {})
        ledger = UploadLedger(checkpoint, save)
        found = {}

        def phase(name, key, run):
            if not verify_all and completed.get(name) == key:
                timeline.skip(name, "completed in an earlier run")
                return {"skipped": True}
            value = run()
            completed[name] = key
            checkpoint["stage"], checkpoint["updatedAt"] = f"{name}_complete", now()
            if save:
                save(checkpoint)
            return value

        def draft_listing():
            if "row" not in found:
                with timeline.step("find draft in Não Protocolados"):
                    found["row"] = self._pending_row(draft_id, author["name"])
            if found["row"] is None:
                raise RuntimeError("The filled draft was not found in Não Protocolados")
            return found["row"]

        def subjects():
            with timeline.step("subjects"):
                return self.add_subjects(draft_url, payload_subjects(payload))

        def parties():
            out = {}
            panel = None
            with timeline.step("party panel"):
                panel = self.party_panel(draft_url)

            def add(step, call, **details):
                # A snapshot only answers "is it there?" until something is added; after that each item re-reads.
                nonlocal panel
                with timeline.step(step, **details):
                    result = call(panel)
                changed = result.get("alreadyAdded") is not True or (result.get("address") or {}).get("changed", False)
                if changed:
                    panel = None
                return result

            out["author"] = add(
                "author",
                lambda p: self.add_author(draft_url, payload["authorCpf"], author["name"], author.get("address"), p),
            )
            if "attorney" in payload:
                out["attorney"] = add("attorney", lambda p: self.add_attorney(draft_url, payload["attorney"], p))
            if "passive" in payload:
                out["passive"] = add("passive body", lambda p: self.add_passive_body(draft_url, payload["passive"], p))
            if "otherParticipants" in payload:
                out["otherParticipants"] = [
                    add(
                        "other participant",
                        lambda p, body=body: self.add_other_participant(draft_url, body, p),
                        party=body.get("name"),
                    )
                    for body in payload["otherParticipants"]
                ]
            return out

        def characteristics():
            with timeline.step("characteristics"):
                return self.save_characteristics(draft_url, payload["characteristics"])

        def documents():
            return self.add_documents(draft_listing()["draftUrl"], payload["documents"], timeline, ledger=ledger)

        prints = phase_fingerprints(payload)
        added = phase("subjects", prints["subjects"], subjects)
        single = "subject" in payload and isinstance(added, list)
        result = {("subject" if "subject" in payload else "subjects"): added[0] if single else added}
        filled = phase("parties", prints["parties"], parties)
        result.update({"parties": filled} if filled.get("skipped") else filled)
        if "characteristics" in payload:
            result["characteristics"] = phase("characteristics", prints["characteristics"], characteristics)
        if "documents" in payload:
            result["documents"] = phase("documents", prints["documents"], documents)
        if "documents" in payload and result["documents"].get("skipped"):
            # The ledger skipped the upload, so confirm in PJe that the documents are still on the draft.
            expected = expected_documents(payload["documents"])
            with timeline.step("documents check"):
                try:
                    self._require_listed(self.review_documents(draft_listing()["draftUrl"]), expected, ledger)
                except RuntimeError:
                    completed.pop("documents", None)  # the next run uploads what is missing
                    if save:
                        save(checkpoint)
                    raise
        draft_listing()
        result["pending"] = {"draftId": draft_id, "inNaoProtocolados": True}
        return result
