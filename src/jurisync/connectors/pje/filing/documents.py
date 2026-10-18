"""Main petition and supporting documents."""

import json
import re
import uuid
from pathlib import Path

from bs4 import BeautifulSoup

from jurisync.connectors.pje import jsf
from jurisync.connectors.pje.filing.checkpoint import UploadLedger
from jurisync.connectors.pje.filing.names import same_name, upload_name
from jurisync.connectors.pje.filing.payload import approved_content
from jurisync.connectors.pje.filing.timeline import StepTimeline
from jurisync.connectors.pje.parsers import jsf_data


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
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 8:
                continue
            file_link = row.find("a", title=re.compile(r"\.pdf\s*\(", re.I))
            if file_link is None:
                continue
            name = re.sub(r"\s*\([^)]*\)$", "", file_link["title"])
            documents.append(
                {
                    "name": name,
                    "type": cells[7].get_text(" ", strip=True),
                    "description": cells[6].get_text(" ", strip=True),
                }
            )
        return documents

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
        content = approved_content(document)  # checked again right before the bytes leave
        ledger.record(document)
        self._html(self.multipart(upload_url, multipart, upload_control + ":file", path, content=content))
        self.post(self.update_url, progress)
        completion = re.search(
            r"onuploadcomplete':function\(event\)\{A4J\.AJAX\.Submit\('formularioUpload',event,\{"
            r"'similarityGroupingId':'(uploadDocumentoPrincipalDecoration:j_id\d+)'",
            html,
        )
        save = re.search(r"gravarDocumentoPdf=function\(\).*?similarityGroupingId':'(j_id\d+)'", html, re.S)
        if not completion or not save:
            raise RuntimeError("PJe did not render the main-document completion actions")
        completed = self.ajax(
            self.update_url, jsf.view_state(html), fields, "_viewRoot", {completion.group(1): completion.group(1)}
        )
        oncomplete = BeautifulSoup(completed, "html.parser").find(id="org.ajax4jsf.oncomplete")
        if oncomplete is None or "gravarDocumentoPdf();" not in oncomplete.get_text():
            raise RuntimeError("PJe did not confirm completion of the main PDF upload")
        saved_response = self.ajax(
            self.update_url, jsf.view_state(html), fields, "_viewRoot", {save.group(1): save.group(1)}
        )
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
        missing = [item for item in documents if not self._is_listed(listed, item, ledger)]
        for item in missing:
            approved_content(item)  # every file, before any upload row is registered
        if missing:
            self._upload_batch(draft_url, missing, timeline, ledger)
            if existing is None:
                self._require_listed(self.review_documents(draft_url), missing)
        return [
            {"alreadyAdded": item not in missing, "name": Path(item["path"]).name, "type": item["type"]}
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

    def _is_listed(self, listed, item, ledger):
        path = Path(item["path"]).expanduser()
        saved = [entry for entry in listed if same_name(entry["name"], path)]
        if not saved:
            return False
        if len(saved) != 1 or saved[0]["type"] != item["type"]:
            raise RuntimeError(f"Draft has {path.name} with a different document type")
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
                _, kind, _ = jsf.select_option(soup, select["name"], item["type"])
                fields = dict(base)
                fields.update(
                    {
                        name: value
                        for name, value in jsf_data(html)[1].items()
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
