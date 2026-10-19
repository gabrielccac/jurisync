"""Main petition and supporting documents."""

import json
import math
import os
import re
import time
import uuid
from pathlib import Path

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing import attachment_types
from jurisync.connectors.pje.filing.checkpoint import UploadLedger
from jurisync.connectors.pje.filing.names import same_name, upload_name
from jurisync.connectors.pje.filing.payload import approved_content
from jurisync.connectors.pje.filing.timeline import StepTimeline
from jurisync.connectors.pje.parsers import jsf_data

SETTLE_INTERVAL = 15.0


def settle_seconds():
    """How long to keep rereading the draft for a file already sent before sending it again (TRF1 may save late)."""
    return float(os.environ.get("PJE_UPLOAD_SETTLE_SECONDS", "90"))


def listed_size_kb(title):
    """The size PJe shows next to a file name, "x.pdf (6.642,34 Kb)", in KiB; None when absent."""
    match = re.search(r"\(([\d.,]+)\s*Kb\)\s*$", title or "", re.I)
    return float(match[1].replace(".", "").replace(",", ".")) if match else None


def same_size(entry, path):
    """A listed file has this file's size, as far as PJe's two-decimal KiB shows it (unknown sizes pass)."""
    return entry.get("sizeKb") is None or abs(entry["sizeKb"] - path.stat().st_size / 1024) <= 0.011


def expected_documents(documents):
    """The payload's documents as the draft should list them: the main petition first, with its type filled in."""
    main = documents["main"]
    return [{**main, "type": main.get("type", "Petição inicial")}, *documents.get("supporting", [])]


class DocumentsMixin:
    def documents_panel(self, draft_url):
        html = self.get(draft_url)
        html = self.ajax(
            self.update_url,
            jsf.view_state(html),
            {},
            "_viewRoot",
            {"novoAnexo": "novoAnexo", "ajaxSingle": "novoAnexo"},
        )
        soup = BeautifulSoup(html, "html.parser")
        pdf = soup.find("input", attrs={"name": "raTipoDocPrincipal", "value": "PDF"})
        if pdf is None:
            raise RuntimeError("PJe did not render the petition PDF mode")
        if pdf.has_attr("checked"):
            return html
        fields = jsf.form_fields(html, "formularioUpload")
        fields["raTipoDocPrincipal"] = "PDF"
        container, action = jsf.ajax_event(pdf)
        refreshed = self.ajax(self.update_url, jsf.view_state(html), fields, container, action)
        replacement = BeautifulSoup(refreshed, "html.parser").find(id="divArquivos")
        original = soup.find(id="divArquivos")
        if replacement is None or original is None:
            raise RuntimeError("PJe did not render the PDF document controls")
        original.replace_with(replacement)
        pdf["checked"] = "checked"
        html_mode = soup.find("input", attrs={"name": "raTipoDocPrincipal", "value": "HTML"})
        if html_mode:
            html_mode.attrs.pop("checked", None)
        for state in soup.find_all("input", attrs={"name": "javax.faces.ViewState"}):
            state["value"] = jsf.view_state(refreshed)
        return str(soup)

    def review_documents(self, draft_url):
        html = self.get(draft_url)
        html = self.ajax(
            self.update_url,
            jsf.view_state(html),
            {},
            "_viewRoot",
            {"informativo": "informativo", "ajaxSingle": "informativo"},
        )
        table = BeautifulSoup(html, "html.parser").find(id="processoDocumentoGridList")
        if table is None:
            raise RuntimeError("PJe did not render the draft document list")
        documents = []
        # As colunas mudam por portal (o TRF1 tem "Id na origem" a mais): localize "Documento" e "Tipo" pelo
        # cabeçalho; sem cabeçalho reconhecível, valem as posições do TRF5.
        heads = [th.get_text(" ", strip=True) for th in table.find_all("th")]
        type_index = heads.index("Tipo") if "Tipo" in heads else 7
        description_index = heads.index("Documento") if "Documento" in heads else 6
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) <= max(type_index, description_index):
                continue
            file_link = row.find("a", title=re.compile(r"\.pdf\s*\(", re.I))
            if file_link is None:
                continue
            name = re.sub(r"\s*\([^)]*\)$", "", file_link["title"])
            entry = {
                "name": name,
                "type": cells[type_index].get_text(" ", strip=True),
                "description": cells[description_index].get_text(" ", strip=True),
            }
            size = listed_size_kb(file_link["title"])
            if size is not None:
                entry["sizeKb"] = size
            documents.append(entry)
        return documents

    def attachment_types(self, draft_url):
        """The types the Anexar tab offers: `main` (the petition's select) and `supporting` (the batch type select;
        None when the portal has none to read)."""
        soup = BeautifulSoup(self.documents_panel(draft_url), "html.parser")
        main = soup.find("select", attrs={"name": lambda name: (name or "").endswith("cbTD")})
        if main is None:
            raise RuntimeError("PJe did not render the attachment type list")
        batch = soup.find("select", attrs={"name": re.compile(r"tipoDocLote")}) or soup.find(
            "select", id=re.compile(r"tipoDocLote")
        )

        def labels(select):
            options = [option.get_text(" ", strip=True) for option in select.find_all("option")]
            return [label for label in options if label and not label.lower().startswith("selecione")]

        return {"main": labels(main), "supporting": labels(batch) if batch is not None else None}

    def check_attachment_types(self, draft_url, documents):
        """Read the portal's types once the draft exists, keep them, and fail on any payload type it does not offer,
        before a single attachment row is registered."""
        types = attachment_types.remember(self.filing.key, self.attachment_types(draft_url))
        main = documents["main"].get("type", "Petição inicial")
        supporting = {Path(item["path"]).name: item["type"] for item in documents.get("supporting", [])}
        problems = attachment_types.unknown_types(types, main, supporting)
        if problems:
            raise RuntimeError(
                "The payload uses attachment types this PJe does not offer, so nothing was attached: "
                + "; ".join(problems)
                + ". Fix the types with pje_filing_build (a new approval follows), then continue with pje_draft_fill"
            )
        return types

    def _await_listing(self, draft_url, documents, ledger, listed, timeline=None):
        """Reread the draft until every document this run already sent is listed, or the settle time runs out.

        After a timed-out POST PJe may still save the file later; sending it again then leaves two copies.
        """
        timeline = timeline or StepTimeline(None, self, None)
        sent = [item for item in documents if ledger.sent(item)]
        for _ in range(math.ceil(settle_seconds() / SETTLE_INTERVAL)):
            if all(self._listed_once(listed, item) for item in sent):
                break
            self.sleep(SETTLE_INTERVAL)
            with timeline.step("reread draft for files already sent"):
                listed = self.review_documents(draft_url)
        return listed

    @staticmethod
    def _listed_once(listed, item):
        path = Path(item["path"]).expanduser()
        return any(same_name(entry["name"], path) for entry in listed)

    def sleep(self, seconds):
        time.sleep(seconds)

    @staticmethod
    def _require_same_content(document, ledger):
        """A name already on the draft counts only when the ledger says this run sent this exact content under it.

        Otherwise fail: PJe would keep the old bytes under the same name, and nothing here removes a saved document.
        """
        problem = ledger.mismatch(document)
        if problem:
            raise RuntimeError(
                f"{problem}. It was not uploaded again: remove it from the draft in PJe (or start a new runId), "
                "then run fill again"
            )

    def add_main_document(self, draft_url, document, existing=None, ledger=None):
        ledger = ledger if ledger is not None else UploadLedger()
        path = Path(document["path"]).expanduser()
        if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError("Main document must be an existing PDF of at most 10 MB")
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("Main document is not a PDF")
        approved_content(document)
        listed = existing if existing is not None else self.review_documents(draft_url)
        listed = self._await_listing(draft_url, [document], ledger, listed)
        main_documents = [item for item in listed if item["type"] == document.get("type", "Petição inicial")]
        if main_documents and len(main_documents) == 1 and same_name(main_documents[0]["name"], path):
            self._require_same_content(document, ledger)
            return {"alreadyAdded": True, "name": path.name}
        # PJe keeps one main petition per draft: uploading a different one overrides the saved one.
        html = self.documents_panel(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        existing = soup.select_one("#divNomeArquivo .outNomeArquivo")
        existing_name = existing.get_text(" ", strip=True) if existing else ""
        if existing_name and same_name(existing_name, path):
            self._require_same_content(document, ledger)
            return {"alreadyAdded": True, "name": path.name}
        if (
            soup.find("input", attrs={"name": "uploadDocumentoPrincipalDecoration:uploadDocumentoPrincipal:file"})
            is None
        ):
            raise RuntimeError("PJe did not render the PDF upload control")
        fields = jsf.form_fields(html, "formularioUpload")
        selector, kind, _ = jsf.select_option(soup, "cbTDDecoration:cbTD", document.get("type", "Petição inicial"))
        description = jsf.field(soup, "ipDescDecoration:ipDesc")
        number = jsf.field(soup, "ipNroDecoration:ipNro")
        mode = soup.find("input", attrs={"name": "raTipoDocPrincipal", "value": "PDF"})
        if mode is None:
            raise RuntimeError("PJe did not render PDF mode for the main document")
        fields[selector["name"]] = kind
        fields[description["name"]] = document.get("description", "Petição inicial")
        fields[number["name"]] = document.get("number", "")
        fields[mode["name"]] = "PDF"
        fields["uploadDocumentoPrincipalDecoration:uploadDocumentoPrincipal:file"] = ""
        self.ajax(
            self.update_url,
            jsf.view_state(html),
            fields,
            "_viewRoot",
            {"carregaProtocolarDocumentoBean": "carregaProtocolarDocumentoBean"},
        )
        upload_control = "uploadDocumentoPrincipalDecoration:uploadDocumentoPrincipal"
        upload_uid = str(uuid.uuid4().int / 10**39)
        progress = [
            ("AJAXREQUEST", "_viewRoot"),
            ("formularioUpload", "formularioUpload"),
            (selector["name"], kind),
            (description["name"], fields[description["name"]]),
            (number["name"], fields[number["name"]]),
            (mode["name"], "PDF"),
            (upload_control + ":file", "C:\\fakepath\\" + upload_name(path)),
            (upload_control + ":file", ""),
            ("javax.faces.ViewState", jsf.view_state(html)),
            ("formularioUpload_link_hidden_", upload_control),
            (upload_control, upload_control),
            ("_richfaces_file_upload_action", "progress"),
            ("_richfaces_upload_uid", upload_uid),
            ("ajaxSingle", upload_control),
            ("AJAX:EVENTS_COUNT", "1"),
        ]
        self.post(self.update_url, progress)
        upload_url = (
            self.update_url + f"?_richfaces_upload_uid={upload_uid}&{upload_control}={upload_control}"
            "&_richfaces_upload_file_indicator=true&AJAXREQUEST=_viewRoot"
        )
        multipart = {
            "formularioUpload": "formularioUpload",
            description["name"]: fields[description["name"]],
            number["name"]: fields[number["name"]],
            mode["name"]: "PDF",
            "javax.faces.ViewState": jsf.view_state(html),
        }
        completion = re.search(
            r"onuploadcomplete':function\(event\)\{A4J\.AJAX\.Submit\('formularioUpload',event,\{"
            r"'similarityGroupingId':'(uploadDocumentoPrincipalDecoration:j_id\d+)'",
            html,
        )
        save = re.search(r"gravarDocumentoPdf=function\(\).*?similarityGroupingId':'(j_id\d+)'", html, re.S)
        if not completion or not save:
            raise RuntimeError("PJe did not render the main-document completion actions")
        content = approved_content(document)  # checked again right before the bytes leave
        ledger.record(document)
        try:
            self._html(self.multipart(upload_url, multipart, upload_control + ":file", path, content=content))
            self.post(self.update_url, progress)
            completed = self.ajax(
                self.update_url, jsf.view_state(html), fields, "_viewRoot", {completion.group(1): completion.group(1)}
            )
            oncomplete = BeautifulSoup(completed, "html.parser").find(id="org.ajax4jsf.oncomplete")
            if oncomplete is None or "gravarDocumentoPdf();" not in oncomplete.get_text():
                raise RuntimeError("PJe did not confirm completion of the main PDF upload")
            saved_response = self.ajax(
                self.update_url, jsf.view_state(html), fields, "_viewRoot", {save.group(1): save.group(1)}
            )
        except OSError as error:
            main = {**document, "type": document.get("type", "Petição inicial")}
            return self._after_timeout(draft_url, [main], ledger, error)[0]
        saved = [
            item
            for item in self.review_documents(draft_url)
            if item["type"] == document.get("type", "Petição inicial") and same_name(item["name"], path)
        ]
        if len(saved) != 1:
            messages = [
                node.get_text(" ", strip=True)
                for node in BeautifulSoup(saved_response, "html.parser").select(".rich-messages-label")
            ]
            detail = f": {'; '.join(messages)}" if messages else ""
            raise RuntimeError(f"PJe did not retain the main PDF on the draft{detail}")
        return {"alreadyAdded": False, "name": path.name}

    def add_supporting_document(self, draft_url, document, existing=None, ledger=None):
        """One supporting PDF: a batch of one."""
        return self.add_supporting_documents(draft_url, [document], existing=existing, ledger=ledger)[0]

    def add_supporting_documents(self, draft_url, documents, existing=None, timeline=None, ledger=None):
        """Upload the supporting PDFs that are not on the draft yet, all in one batch.

        With `existing` (a documents listing) no listing is read here and the caller verifies the whole set.
        """
        timeline = timeline or StepTimeline(None, self, None)
        ledger = ledger if ledger is not None else UploadLedger()
        listed = existing if existing is not None else self.review_documents(draft_url)
        listed = self._await_listing(draft_url, documents, ledger, listed, timeline)
        missing = [item for item in documents if not self._is_listed(listed, item, ledger)]
        confirmed = set()
        for item in missing:
            approved_content(item)  # every file, before any upload row is registered
        if missing:
            try:
                self._upload_batch(draft_url, missing, timeline, ledger)
            except OSError as error:
                confirmed = {item["name"] for item in self._after_timeout(draft_url, missing, ledger, error, timeline)}
            else:
                if existing is None:
                    self._require_listed(self.review_documents(draft_url), missing)
        return [
            {
                "alreadyAdded": item not in missing,
                "name": Path(item["path"]).name,
                "type": item["type"],
                **({"confirmedAfterError": True} if Path(item["path"]).name in confirmed else {}),
            }
            for item in documents
        ]

    @staticmethod
    def _missing_documents(listed, documents):
        """Names of the documents that do not appear exactly once on the draft with their type."""
        missing = []
        for document in documents:
            path = Path(document["path"]).expanduser()
            saved = [item for item in listed if same_name(item["name"], path) and item["type"] == document["type"]]
            if len(saved) != 1:
                missing.append(path.name)
        return missing

    def _require_listed(self, listed, documents, ledger=None):
        """Every document must appear exactly once on the draft with its type (and, given a ledger, its content)."""
        missing = self._missing_documents(listed, documents)
        if missing:
            raise RuntimeError(f"PJe did not retain these documents on the draft: {', '.join(missing)}")
        for document in documents if ledger is not None else ():
            self._require_same_content(document, ledger)

    def _after_timeout(self, draft_url, documents, ledger, error, timeline=None):
        """A POST failed or timed out mid-upload: wait for PJe to list what was sent; never send it again blindly."""
        listed = self._await_listing(draft_url, documents, ledger, self.review_documents(draft_url), timeline)
        missing = [item for item in documents if not self._is_listed(listed, item, ledger)]
        if missing:
            names = ", ".join(Path(item["path"]).name for item in missing)
            raise RuntimeError(
                f"PJe did not answer an attachment request ({type(error).__name__}: {str(error)[:120]}); after "
                f"rereading the draft these are not on it yet: {names}. Run pje_draft_fill again later: a file already "
                "sent is looked for on the draft again before it is resent"
            ) from error
        return [
            {"alreadyAdded": False, "name": Path(item["path"]).name, "confirmedAfterError": True} for item in documents
        ]

    def _is_listed(self, listed, item, ledger):
        """Whether the draft already has this document: same name once, same type and size, and this content."""
        path = Path(item["path"]).expanduser()
        saved = [entry for entry in listed if same_name(entry["name"], path)]
        if not saved:
            return False
        if len(saved) > 1:
            raise RuntimeError(
                f"{path.name} is on the draft {len(saved)} times (an upload PJe saved after a timeout?): remove the "
                "extra copies in PJe, then run pje_draft_fill again"
            )
        if saved[0]["type"] != item["type"]:
            raise RuntimeError(f"Draft has {path.name} with a different document type")
        if not same_size(saved[0], path):
            raise RuntimeError(f"Draft has {path.name} with another size than the approved file")
        self._require_same_content(item, ledger)
        return True

    @staticmethod
    def _row_name(select):
        """Name of the file whose row holds this type select: the closest ancestor with exactly one file row."""
        node = select
        while node is not None and getattr(node, "find_all", None):
            rows = ([node] if node.has_attr("data-dz-name") else []) + node.find_all(attrs={"data-dz-name": True})
            if len(rows) == 1:
                return rows[0]["data-dz-name"]
            node = node.parent
        return None

    def _upload_batch(self, draft_url, items, timeline, ledger):
        """Register every missing supporting file in one postback, then set type, upload and save each row.

        All of it runs in one page session: rows registered here exist only in that session, so a reload loses them.
        """
        html = self.documents_panel(draft_url)
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", id="formularioUpload")
        cid = form.find("input", attrs={"name": "cid"}) if form else None
        script = soup.find("script", id="acaoAjaxAdicionarProcessoDocumento")
        container = re.search(r"containerId':'([^']+)'", script.get_text()) if script else None
        if cid is None or not cid.get("value") or not container:
            raise RuntimeError("PJe did not render the supporting-document upload controls")
        paths = [Path(item["path"]).expanduser() for item in items]
        for path in paths:
            if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size > 10_000_000:
                raise ValueError(f"Supporting document must be an existing PDF of at most 10 MB: {path}")
            with path.open("rb") as source:
                if source.read(5) != b"%PDF-":
                    raise ValueError(f"Supporting document is not a PDF: {path.name}")
            if any(same_name(node.get("data-dz-name") or "", path) for node in soup.select("[data-dz-name]")):
                raise RuntimeError(f"Supporting document {path.name} has an unfinished upload row")
        main_name = soup.select_one("#divNomeArquivo .outNomeArquivo")
        if main_name is None or not main_name.get_text(" ", strip=True):
            raise RuntimeError("Add the main petition PDF before supporting documents")
        row_names = {
            node.get("name") for node in form.find_all("select") if (node.get("name") or "").endswith(":tipoDoc")
        }
        base = jsf.form_fields(html, "formularioUpload")
        registration = json.dumps(
            {
                "array": json.dumps(
                    [
                        {"nome": upload_name(path), "tamanho": path.stat().st_size, "mime": "application/pdf"}
                        for path in paths
                    ],
                    ensure_ascii=False,
                )
            },
            ensure_ascii=False,
        )
        html = self.ajax(
            self.update_url,
            jsf.view_state(html),
            base,
            container.group(1),
            {
                "jsonProcessoDocumento": registration,
                "acaoAjaxAdicionarProcessoDocumento": "acaoAjaxAdicionarProcessoDocumento",
                "ajaxSingle": "acaoAjaxAdicionarProcessoDocumento",
            },
        )
        soup = BeautifulSoup(html, "html.parser")
        # O TRF1 só mostra os botões da linha (atualizar tipo, gravar) na resposta do registro; as respostas
        # seguintes são parciais e não trazem a linha. Os ids são estáveis, então os buscamos aqui se faltarem depois.
        registered = soup
        registered_html = html
        selects = [
            node
            for node in soup.find_all("select")
            if (node.get("name") or "").endswith(":tipoDoc") and node.get("name") not in row_names
        ]
        pairs = []
        for select in selects:
            name = self._row_name(select)
            match = next(
                (item for item, path in zip(items, paths, strict=True) if name and same_name(name, path)), None
            )
            if match is None:
                raise RuntimeError(f"PJe row {name!r} does not match a file to upload")
            pairs.append((select, match, Path(match["path"]).expanduser()))
        if sorted(item["path"] for _, item, _ in pairs) != sorted(item["path"] for item in items):
            raise RuntimeError("PJe did not register one row for each missing supporting document")
        for select, item, path in pairs:
            with timeline.step("supporting document", document=path.name):
                prefix = select["name"].removesuffix(":tipoDoc")
                soup = BeautifulSoup(html, "html.parser")
                # Depois de gravar uma linha o TRF1 devolve só um trecho da página: as demais linhas (select de tipo,
                # descrição, ordem) ficam na resposta do registro.
                row_source = soup if soup.find("select", attrs={"name": select["name"]}) else registered
                row_html = html if row_source is soup else registered_html
                _, kind, _ = jsf.select_option(row_source, select["name"], item["type"])
                fields = dict(base)
                fields.update(
                    {
                        name: value
                        for name, value in jsf_data(row_html)[1].items()
                        if re.match(r".+:\d+:(ordem|tipoDoc|descDoc|numeroDoc)$", name)
                    }
                )
                fields[select["name"]] = kind
                fields[prefix + ":descDoc"] = item.get("description", path.stem)
                fields[prefix + ":numeroDoc"] = item.get("number", "")
                container_id, action = jsf.ajax_event(select)
                html = self.ajax(self.update_url, jsf.view_state(html), fields, container_id, action)
                fields["javax.faces.ViewState"] = jsf.view_state(html)
                upload_url = self.portal.base_url + "seam/resource/upload?cid=" + cid["value"]
                content = approved_content(item)  # checked again right before the bytes leave
                ledger.record(item)
                response = self.multipart(upload_url, fields, "file[]", path, content=content, resource=True)
                confirmation = response.json()
                files = confirmation.get("arquivos", [])
                if not confirmation.get("requisicao") or len(files) != 1 or not files[0].get("upload"):
                    raise RuntimeError(f"PJe did not accept the supporting PDF bytes: {path.name}")
                soup = BeautifulSoup(html, "html.parser")
                update = soup.find(id=prefix + ":commandLinkAtualizarComboTipoDocumento") or registered.find(
                    id=prefix + ":commandLinkAtualizarComboTipoDocumento"
                )
                save = soup.find(id=prefix + ":commandLinkGravar") or registered.find(id=prefix + ":commandLinkGravar")
                if update is None or save is None:
                    raise RuntimeError(f"PJe did not render row actions for {path.name}")
                fields.pop("javax.faces.ViewState")
                container_id, action = jsf.ajax_event(update)
                self.ajax(self.update_url, jsf.view_state(html), fields, container_id, action)
                container_id, action = jsf.ajax_event(save)
                html = self.ajax(self.update_url, jsf.view_state(html), fields, container_id, action)

    def add_documents(self, draft_url, documents, timeline=None, ledger=None):
        """Upload what is missing: the main petition, then every missing supporting file in one batch.

        `ledger` (an UploadLedger over the run's checkpoint) records what content went up under which name; without
        one, any document already on the draft is refused because its content cannot be confirmed.
        """
        timeline = timeline or StepTimeline(None, self, None)
        ledger = ledger if ledger is not None else UploadLedger()
        with timeline.step("documents snapshot"):
            listed = self.review_documents(draft_url)
        main_item = documents["main"]
        with timeline.step("main document", document=Path(main_item["path"]).name):
            main = self.add_main_document(draft_url, main_item, existing=listed, ledger=ledger)
        supporting_items = documents.get("supporting", [])
        supporting = self.add_supporting_documents(
            draft_url, supporting_items, existing=listed, timeline=timeline, ledger=ledger
        )
        if not (main["alreadyAdded"] and all(item["alreadyAdded"] for item in supporting)):
            with timeline.step("documents verify"):
                self._require_listed(self.review_documents(draft_url), expected_documents(documents), ledger)
        return {"main": main, "supporting": supporting}
