"""PJe-JT (PJe-KZ) REST adapter, from sanitized API fixtures: counters, inbox, process, documents, login guard."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jurisync import portals
from jurisync.connectors.pje import acervo, inbox, kz, process
from jurisync.connectors.pje.auth import certificate, sessions
from jurisync.connectors.pje.expedientes import NoticeUnchecked, PendingNotice

FIXTURES = Path(__file__).parent / "fixtures" / "kz"
NUMBER = "0000001-00.2026.5.07.0001"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def token():
    claims = base64.urlsafe_b64encode(json.dumps({"id": 42, "instancia": 1}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


class Response:
    def __init__(self, status=200, body=None, text=None, content=b"", content_type="application/json"):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def iter_content(self):
        yield self.content

    def close(self):
        pass


class Session:
    """Answers the API paths with the fixtures and records every request."""

    def __init__(self, token_status=201, pdf=b"%PDF-1.4 fake", expedientes=None, push=None):
        self.calls = []
        self.token_status = token_status
        self.pdf = pdf
        self.expedientes = expedientes or (lambda params: fixture("expedientes"))
        self.push = push

    def get(self, url, params=None, headers=None, **kwargs):
        path = url.split("pje.trt7.jus.br", 1)[1]
        self.calls.append((path, dict(params or {})))
        params = params or {}
        if path.endswith("/token/refresh"):
            return Response(self.token_status, {"access_token": token()})
        if path.endswith("/totalizadores"):
            return Response(body=fixture("totalizadores"))
        if path.endswith("/orgaojulgadores"):
            return Response(
                body=[{"id": 1, "descricao": "Vara do Trabalho de Teste", "descricaoJurisdicao": "Cidade Teste"}]
            )
        if path.endswith("/paineladvogado/42/processos"):
            body = fixture("processos_expedientes" if params["idPainelAdvogadoEnum"] == 2 else "processos_acervo")
            return Response(body={**body, "pagina": params["pagina"]})
        if path.endswith("/api/push"):
            if params.get("tipoConsulta") != "PROCESSOS_CADASTRADOS":
                return Response(
                    body={
                        "pagina": 1,
                        "tamanhoPagina": 50,
                        "qtdPaginas": 1,
                        "totalRegistros": 1,
                        "resultado": [{"idProcesso": 7, "numeroProcesso": "0000009-00.2026.5.07.0009"}],
                    }
                )
            if self.push:
                return Response(body=self.push(params))
            return Response(body={**fixture("push"), "pagina": params["pagina"]})
        if path.endswith("/api/processos"):
            return Response(text=("1000001" if params["numero"] == NUMBER else ""))
        if path.endswith("/processos/id/1000001"):
            return Response(body=fixture("processo"))
        if path.endswith("/partes"):
            return Response(body=fixture("partes"))
        if path.endswith("/timeline"):
            return Response(body=fixture("timeline"))
        if path.endswith("/expedientes"):
            return Response(body=self.expedientes(params))
        if "/documentos/id/" in path:
            return Response(content=self.pdf, content_type="application/pdf")
        return Response(404, {"codigoErro": "X"})


TRT_PORTALS = ["trt12-1g", "trt12-2g", "trt15-1g", "trt15-2g", "trt1-1g", "trt1-2g", "trt3-1g", "trt3-2g"]


def test_only_the_judicial_portals_use_the_rest_flavor():
    kz_portals = {key for key in portals.keys("pje") if portals.pje(key).kz}
    assert kz_portals == {
        "trt7-1g",
        "trt7-2g",
        "trt18-1g",
        "trt18-2g",
        "trt12-1g",
        "trt12-2g",
        "trt15-1g",
        "trt15-2g",
        "trt1-1g",
        "trt1-2g",
        "trt3-1g",
        "trt3-2g",
        "trt5-1g",
        "trt5-2g",
        "trt20-1g",
        "trt20-2g",
        "trt6-1g",
        "trt6-2g",
        "trt10-1g",
        "trt10-2g",
        "trt17-1g",
        "trt17-2g",
        "trt19-1g",
        "trt19-2g",
        "trt23-1g",
        "trt23-2g",
        "trt24-1g",
        "trt24-2g",
        "trt4-1g",
        "trt9-1g",
        "trt2-1g",
        "trt2-2g",
        "tst",
    }
    for key in ("trf5-1g", "trf5-2g", "tjce-1g", "trf1-1g", "trf3-1g", "tjpe-1g"):
        assert not portals.pje(key).kz


def test_counters_come_from_the_rest_totals():
    session = Session()
    result = inbox.read_counters("trt7-1g", session=session)
    assert result["success"] and result["portal"] == "trt7-1g"
    assert result["buckets"][0] == {"index": 1, "label": "Acervo Geral", "count": 5}
    assert [b["label"] for b in result["buckets"]] == ["Acervo Geral", "Meus Expedientes", "Arquivados"]
    assert all(call[0].startswith(("/pje-comum-api", "/pje-seguranca")) for call in session.calls)


def test_counters_tree_lists_the_bodies_of_non_empty_buckets():
    result = inbox.read_counters("trt7-1g", session=Session(), tree=True)
    regions = result["buckets"][0]["regions"]
    assert regions[0]["path"] == ["Acervo Geral", "Cidade Teste", "Vara do Trabalho de Teste"]


def test_default_inbox_is_meus_expedientes_with_ciencia_state():
    result = inbox.read_inbox("trt7-1g", session=Session())
    assert result["bucket"]["label"] == "Meus Expedientes" and result["complete"]
    (box,) = result["inboxes"]
    assert box["path"] == ["Meus Expedientes", "Vara do Trabalho de Teste"]
    (row,) = box["rows"]
    assert row["expedienteId"] == "7000001"
    assert row["processNumber"] == NUMBER and row["processId"] == "1000001"
    assert row["ciencia"] == {"por": None, "em": "2026-11-06"} and not row["cienciaPendente"]
    assert row["limite"] == "2026-11-18" and row["respondido"] is False
    assert "AUTOR" not in json.dumps(row)


def test_inbox_rows_take_type_recipient_and_days_from_the_process_expedientes():
    session = Session()
    (row,) = inbox.read_inbox("trt7-1g", session=session)["inboxes"][0]["rows"]
    assert row["tipo"] == "Intimação" and row["parte"] == "REU TESTE" and row["meio"]
    assert "detalhesIndisponiveis" not in row
    assert sum(path.endswith("/processos/id/1000001/expedientes") for path, _ in session.calls) == 1


def test_inbox_rows_stay_usable_when_the_expedientes_read_fails(monkeypatch):
    def refuse(self, path, params=None, *, label):
        if path.endswith("/expedientes"):
            raise RuntimeError("PJe expedientes returned HTTP 500")
        return original(self, path, params, label=label)

    original = kz.Api.json
    monkeypatch.setattr(kz.Api, "json", refuse)
    (row,) = inbox.read_inbox("trt7-1g", session=Session())["inboxes"][0]["rows"]
    assert row["expedienteId"] == "7000001" and row["tipo"] is None and row["detalhesIndisponiveis"]


def test_inbox_filters_and_regions():
    session = Session()
    result = inbox.read_inbox("trt7-1g", filters={"processNumber": NUMBER, "from": "2026-11-05"}, session=session)
    assert result["inboxes"] == [] or result["inboxes"][0]["rows"] == []
    assert any(params.get("numeroProcesso") == NUMBER for _, params in session.calls)
    assert inbox.read_inbox("trt7-1g", regions=["Outra Vara"], session=Session())["inboxes"] == []
    with pytest.raises(ValueError):
        inbox.read_inbox("trt7-1g", filters={"oab": "x"}, session=Session())


def test_acervo_rows_are_not_expedientes():
    result = inbox.read_inbox("trt7-1g", bucket="Acervo", session=Session())
    rows = result["inboxes"][0]["rows"]
    assert len(rows) == 2 and all(r["expedienteId"] is None and not r["cienciaPendente"] for r in rows)


def test_process_read_keeps_the_existing_shape():
    result = process.fetch_process(NUMBER, "page", portal="trt7-1g", session=Session())
    assert result["success"] and result["processNumber"] == NUMBER and result["processo_id"] == "1000001"
    assert result["metadata"]["judicial_class"] == "Ação Trabalhista - Rito Ordinário (ATOrd)"
    assert result["parties"]["active"] == ["AUTOR TESTE (RECLAMANTE)"]
    assert result["timeline"][0]["date"] == "05 nov. 2026"
    ids = {d["id"] for d in result["documentInventory"]}
    assert "9100002" in ids and "9000005" in ids
    assert result["expedientes"]["total"] == 3
    assert isinstance(result["expedientes"]["abertos"], list)
    pending = [e for e in result["expedientes"]["items"] if e["ciencia"] is None]
    assert [e["expedienteId"] for e in pending] == ["7000003"]
    assert not result["pagination"]["more_pages_possible"] and result["pagination"]["complete"]


def test_unknown_process_is_reported_not_raised():
    result = process.fetch_process("0000009-00.2026.5.07.0001", "page", portal="trt7-1g", session=Session())
    assert result["success"] is False


def test_document_read_uses_the_pdf_route_and_extracts(monkeypatch):
    monkeypatch.setattr(
        kz, "extract_document_text", lambda content, kind, number, allow_unnumbered: ("extracted", "texto")
    )
    session = Session()
    result = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)
    assert result["documents"][0]["extractionStatus"] == "extracted" and result["documents"][0]["text"] == "texto"
    assert any(path.endswith("/processos/id/1000001/documentos/id/9000005/conteudo") for path, _ in session.calls)
    with pytest.raises(ValueError):
        process.read_documents(NUMBER, ["123"], portal="trt7-1g", session=session)


def test_a_failed_document_read_names_the_step_and_error_class_only(monkeypatch):
    from curl_cffi.requests.exceptions import RequestException

    def interrupted(*args):
        raise RequestException("https://pje.trt7.jus.br/secret?token=x")

    def unwritable(name, content):
        raise PermissionError("/private/path")

    monkeypatch.setattr(kz, "fetch_document", interrupted)
    document = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=Session())["documents"][0]
    assert (document["extractionStatus"], document["failure"]) == ("request_failed", "request failed: RequestException")

    monkeypatch.setattr(
        kz, "fetch_document", lambda *args: {"status": 200, "content_type": "", "bytes": 1, "content": b"x"}
    )
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "texto"))
    monkeypatch.setattr(kz, "save_document", unwritable)
    document = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=Session(), save=True)["documents"][
        0
    ]
    assert (document["failure"], document["text"]) == ("save failed: PermissionError", "")

    monkeypatch.setattr(
        kz, "fetch_document", lambda *args: {"status": 403, "content_type": "", "bytes": 0, "content": None}
    )
    document = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=Session())["documents"][0]
    assert (document["extractionStatus"], document["failure"]) == ("http_error", "HTTP 403")


def test_act_of_a_pending_expediente_is_refused_without_acknowledgement(monkeypatch):
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "ato"))
    session = Session()
    # The pending expediente's document (9100003) is not requested at all: the guard fires first.
    result = kz.search_process(session, NUMBER, portal="trt7-1g", ato_expediente="7000003")
    assert "ato" not in result["ato"] and "error" in result["ato"]
    assert result["ciencia"]["estado"] == "pendente" and result["ciencia"]["intimacoes"][0]["expedienteId"] == "7000003"
    assert not any("/documentos/id/" in path for path, _ in session.calls)
    allowed = kz.search_process(Session(), NUMBER, portal="trt7-1g", ato_expediente="7000003", ato_allow_pending=True)
    assert allowed["ato"]["texto"] == "ato" and allowed["ciencia"]["estado"] == "dada"


def test_pending_document_is_refused_by_read_documents(monkeypatch):
    original = kz.search_process

    def with_pending_document(*args, **kwargs):
        result = original(*args, **kwargs)
        result["document_inventory"].append({"id": "9100003", "title": "Intimação"})
        return result

    monkeypatch.setattr(process.search, "search_process", with_pending_document)
    with pytest.raises(PendingNotice):
        process.read_documents(NUMBER, ["9100003"], portal="trt7-1g", session=Session())


def test_every_request_is_a_get_of_the_api():
    session = Session()
    process.fetch_process(NUMBER, "page", portal="trt7-1g", session=session)
    assert not hasattr(session, "post") and all(path.startswith("/pje-") for path, _ in session.calls)


def test_session_check_uses_the_token_endpoint():
    assert sessions.is_authenticated(Session(), "trt7-1g") is True
    assert sessions.is_authenticated(Session(token_status=401), "trt7-1g") is False


def test_login_rejects_the_pre_registration_page():
    config = portals.pje("tst")
    page = SimpleNamespace(url="https://pje.tst.jus.br/tst/publico/usuario/cadastro.seam")
    with pytest.raises(RuntimeError, match="not registered"):
        certificate._require_registered(page, config)
    certificate._require_registered(SimpleNamespace(url="https://pje.tst.jus.br/tst/Painel/x.seam"), config)


def test_push_registrations_come_from_the_rest_endpoint_in_the_jsf_shape():
    from jurisync.connectors.pje import push

    session = Session()
    result = push.read_push("trt7-1g", session=session)
    assert result["success"] and result["complete"] and result["rows_returned"] == 2
    assert result["results_count"] == 2 and result["pagination"]["total_pages"] == 1
    assert result["items"][0] == {
        "process_number": "0000001-00.2026.5.07.0001",
        "registered_at": "2026-10-01",
        "alternate_email": "",
        "observation": "",
        "process_id": 1000001,
    }
    push_calls = [params for path, params in session.calls if path.endswith("/api/push")]
    assert push_calls and all(p["tipoConsulta"] == "PROCESSOS_CADASTRADOS" for p in push_calls)
    assert not any("listView" in path for path, _ in session.calls)
    assert (result["total"], result["hasMore"], result["offset"], result["limit"]) == (2, False, 0, 100)


def paged_expedientes(params):
    return fixture(f"expedientes_pagina_{params.get('pagina', 1)}")


def expediente_calls(session):
    return [params for path, params in session.calls if path.endswith("/expedientes")]


def test_expedientes_are_read_page_by_page_until_the_reported_total():
    session = Session(expedientes=paged_expedientes)
    items, complete = kz.read_expedientes(kz.Api(session, "trt7-1g"), "1000001")
    assert complete and [item["expedienteId"] for item in items] == ["7000011", "7000012", "7000013"]
    # The first request goes as the app sends it; only the later pages carry the page parameter.
    assert expediente_calls(session) == [{"instancia": 1}, {"instancia": 1, "pagina": 2}]
    result = process.fetch_process(NUMBER, "page", portal="trt7-1g", session=Session(expedientes=paged_expedientes))
    assert result["expedientes"]["total"] == 3 and result["expedientes"]["complete"] is True


def test_a_pending_expediente_beyond_the_first_page_still_blocks_its_document(monkeypatch):
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "texto"))
    session = Session(expedientes=paged_expedientes)
    with pytest.raises(PendingNotice, match="7000013"):
        process.read_documents(NUMBER, ["9000007"], portal="trt7-1g", session=session)
    assert not any("/documentos/id/" in path for path, _ in session.calls)
    # A document of the same process that is neither that act nor attached to it is still read.
    assert process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)["success"]


def test_an_attachment_of_a_pending_act_is_refused_like_the_act(monkeypatch):
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "texto"))
    session = Session(expedientes=paged_expedientes)
    with pytest.raises(PendingNotice, match="attachment"):
        process.read_documents(NUMBER, ["9000008"], portal="trt7-1g", session=session)
    assert not any("/documentos/id/" in path for path, _ in session.calls)
    allowed = process.read_documents(NUMBER, ["9000008"], portal="trt7-1g", session=session, acknowledge_ciencia=True)
    assert allowed["ciencia"]["estado"] == "dada" and allowed["ciencia"]["intimacoes"][0]["expedienteId"] == "7000013"


@pytest.mark.parametrize(
    "answer",
    [
        lambda params: fixture("expedientes_pagina_1"),  # the page parameter is ignored: page 1 comes back again
        lambda params: {**fixture("expedientes"), "totalRegistros": 25},  # fewer rows than the portal reports
        lambda params: {"resultado": fixture("expedientes")["resultado"]},  # no total to compare with
    ],
)
def test_expedientes_that_cannot_be_proven_complete_refuse_every_document_read(monkeypatch, answer):
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "texto"))
    session = Session(expedientes=answer)
    assert kz.read_expedientes(kz.Api(session, "trt7-1g"), "1000001")[1] is False
    with pytest.raises(NoticeUnchecked, match="acknowledgeCiencia=true"):
        process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)
    assert not any("/documentos/id/" in path for path, _ in session.calls)
    result = process.fetch_process(NUMBER, "page", portal="trt7-1g", session=Session(expedientes=answer))
    assert result["expedientes"]["complete"] is False
    assert any(d.get("code") == "expedientes_incomplete" for d in result["diagnostics"])
    allowed = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session, acknowledge_ciencia=True)
    assert allowed["success"] and allowed["ciencia"]["estado"] == "desconhecida"


def test_unreadable_expedientes_refuse_the_document_read(monkeypatch):
    def refuse(self, path, params=None, *, label):
        if path.endswith("/expedientes"):
            raise RuntimeError("PJe expedientes returned HTTP 500")
        return original(self, path, params, label=label)

    original = kz.Api.json
    monkeypatch.setattr(kz.Api, "json", refuse)
    session = Session()
    with pytest.raises(NoticeUnchecked, match="ciência not checked"):
        process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)
    assert not any("/documentos/id/" in path for path, _ in session.calls)


def test_filters_are_applied_before_the_per_process_reads():
    session = Session()
    assert inbox.read_inbox("trt7-1g", regions=["Outra Vara"], session=session)["inboxes"] == []
    assert expediente_calls(session) == []
    session = Session()
    assert inbox.read_inbox("trt7-1g", filters={"expedienteId": "1"}, session=session)["inboxes"] == []
    assert expediente_calls(session) == []
    session = Session()
    inbox.read_inbox("trt7-1g", filters={"expedienteId": "7000001"}, session=session)
    assert len(expediente_calls(session)) == 1


def test_a_bucket_id_the_panel_does_not_have_is_an_error_that_lists_the_valid_ones():
    with pytest.raises(ValueError, match=r"no bucket '0'.*1 \(Acervo Geral\), 2 \(Meus Expedientes\)"):
        inbox.read_inbox("trt7-1g", bucket="0", session=Session())
    assert inbox.read_inbox("trt7-1g", bucket="2", session=Session())["bucket"]["label"] == "Meus Expedientes"
    assert inbox.read_inbox("trt7-1g", session=Session())["bucket"]["index"] == kz.MEUS_EXPEDIENTES


def test_counters_can_expand_one_bucket_only():
    session = Session()
    result = inbox.read_counters("trt7-1g", session=session, bucket="Acervo")
    assert "regions" in result["buckets"][0] and all("regions" not in b for b in result["buckets"][1:])
    assert sum(path.endswith("/orgaojulgadores") for path, _ in session.calls) == 1
    with pytest.raises(ValueError, match="valid buckets"):
        inbox.read_counters("trt7-1g", session=Session(), bucket="9")


def push_pages(total, size=50):
    def answer(params):
        first = (params["pagina"] - 1) * size
        rows = [
            {"idProcesso": n, "numeroProcesso": f"{n:07d}-00.2026.5.07.0001", "dataInclusao": "2026-10-01T10:00:00"}
            for n in range(first + 1, min(first + size, total) + 1)
        ]
        return {
            "pagina": params["pagina"],
            "tamanhoPagina": size,
            "qtdPaginas": -(-total // size),
            "totalRegistros": total,
            "resultado": rows,
        }

    return answer


def push_calls(session):
    return [params["pagina"] for path, params in session.calls if path.endswith("/api/push")]


def test_push_returns_a_window_and_stops_paging_when_it_has_enough():
    from jurisync.connectors.pje import push

    session = Session(push=push_pages(430))
    result = push.read_push("trt7-1g", session=session)
    assert push_calls(session) == [1, 2]
    assert (result["rows_returned"], result["total"], result["hasMore"]) == (100, 430, True)
    session = Session(push=push_pages(430))
    result = push.read_push("trt7-1g", session=session, limit=50, offset=400)
    assert push_calls(session) == list(range(1, 10))
    assert [item["process_id"] for item in result["items"]] == list(range(401, 431))
    assert (result["total"], result["hasMore"], result["offset"]) == (430, False, 400)


def test_a_push_walk_by_offset_reads_each_page_once():
    from jurisync.connectors.pje import push

    session = Session(push=push_pages(430))
    windows = [push.read_push("trt7-1g", session=session, limit=50, offset=offset) for offset in range(0, 430, 50)]
    assert push_calls(session) == list(range(1, 10))
    assert [w["items"][0]["process_id"] for w in windows] == list(range(1, 430, 50))
    assert [w["hasMore"] for w in windows] == [True] * 8 + [False]
    found = push.read_push("trt7-1g", session=session, process_number="0000101-00.2026.5.07.0001")
    assert (push_calls(session), found["total"]) == (list(range(1, 10)), 1)


def test_push_process_filter_reads_the_whole_registry():
    from jurisync.connectors.pje import push

    session = Session(push=push_pages(120))
    wanted = "0000101-00.2026.5.07.0001"
    result = push.read_push("trt7-1g", session=session, limit=10, process_number=wanted)
    assert push_calls(session) == [1, 2, 3]
    assert [item["process_number"] for item in result["items"]] == [wanted]
    assert (result["total"], result["hasMore"]) == (1, False)
    missing = push.read_push(
        "trt7-1g", session=Session(push=push_pages(120)), process_number="0000999-00.2026.5.07.0001"
    )
    assert (missing["total"], missing["items"]) == (0, [])
    for bad in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"process_number": "123"}):
        with pytest.raises(ValueError):
            push.read_push("trt7-1g", session=Session(), **bad)


def test_fixtures_carry_no_real_looking_cpf():
    import re

    for path in FIXTURES.glob("*.json"):
        for cpf in re.findall(r"\d{3}\.\d{3}\.\d{3}-\d{2}", path.read_text(encoding="utf-8")):
            assert len(set(re.sub(r"\D", "", cpf))) == 1, f"{path.name} has a CPF that is not an obvious placeholder"


def test_trt_portals_use_the_national_pattern():
    for key in TRT_PORTALS:
        number, grade = key.removeprefix("trt").split("-")
        config = portals.pje(key)
        assert (config.host, config.region, config.justice, config.kz) == (
            f"pje.trt{number}.jus.br",
            f"{int(number):02d}",
            "5",
            True,
        )
        assert config.root == ("/primeirograu/" if grade == "1g" else "/segundograu/")
        assert config.env_prefix == f"TRT{number}_{grade.upper()}"
        assert portals.pje(key).auth == "certificate_totp"


def test_reading_documents_after_the_process_reuses_its_lookup_but_rereads_the_expedientes(monkeypatch):
    """get_process then read_documents on one session: the lookup, record, parties and timeline are not asked
    again within MEMO_SECONDS; the expedientes (ciência state) always are."""
    monkeypatch.setattr(kz, "extract_document_text", lambda *a, **k: ("extracted", "texto"))
    clock = [1000.0]
    monkeypatch.setattr(kz.time, "monotonic", lambda: clock[0])
    session = Session()
    process.fetch_process(NUMBER, "page", portal="trt7-1g", session=session)
    first = len(session.calls)
    session.calls.clear()
    result = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)
    assert result["documents"][0]["extractionStatus"] == "extracted"
    paths = [path for path, _ in session.calls]
    assert not any(path.endswith(("/api/processos", "/processos/id/1000001", "/partes", "/timeline")) for path in paths)
    assert any(path.endswith("/expedientes") for path in paths)
    assert len(paths) < first

    clock[0] += kz.MEMO_SECONDS + 1
    session.calls.clear()
    process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)
    assert any(path.endswith("/timeline") for path, _ in session.calls)  # expired: looked up again


def test_a_read_whose_parties_failed_is_not_remembered(monkeypatch):
    session = Session()
    original = session.get

    def no_parties(url, params=None, headers=None, **kwargs):
        if url.endswith("/partes"):
            session.calls.append((url, {}))
            return Response(500, {"codigoErro": "X"})
        return original(url, params, headers, **kwargs)

    session.get = no_parties
    process.fetch_process(NUMBER, "page", portal="trt7-1g", session=session)
    session.calls.clear()
    process.fetch_process(NUMBER, "page", portal="trt7-1g", session=session)
    assert any(path.endswith("/timeline") for path, _ in session.calls)


def test_a_document_may_take_minutes_before_its_first_byte():
    session = Session()
    seen = []
    original = session.get

    def recording(url, params=None, headers=None, **kwargs):
        seen.append(kwargs.get("timeout"))
        return original(url, params, headers, **kwargs)

    session.get = recording
    kz.fetch_document(kz.Api(session, "trt7-1g"), "1000001", "9000005")
    assert seen[-1] == kz.DOCUMENT_TIMEOUT and kz.DOCUMENT_TIMEOUT[1] >= 120


def test_acervo_listing_reads_acervo_geral_and_arquivados_once_per_process():
    session = Session()
    result = acervo.read_acervo("trt7-1g", session=session, max_pages=5)
    listed = [params["idPainelAdvogadoEnum"] for path, params in session.calls if path.endswith("/processos")]
    assert listed == [1, 5]  # Acervo Geral and Arquivados; never Meus Expedientes
    assert result["complete"] and result["returned"] == 2
    item = result["items"][0]
    assert set(item) == {
        "processNumber", "processId", "classe", "partes", "orgaoJulgador", "distribuidoEm", "arquivadoEm",
        "jurisdicao", "caixa", "arquivado",
    }  # fmt: skip
    assert item["caixa"] == "Acervo Geral" and item["arquivado"] is False
    assert not any("expedientes" in path or "documento" in path for path, _ in session.calls)


ARQ_512 = {"codigoErro": "ARQ-512", "mensagem": "Erro de mapeamento de objetos"}


def arq_512():
    return Response(500, ARQ_512, content=json.dumps(ARQ_512).encode())


def html_session(answers):
    """A session whose document requests are answered, in order, by ``answers(accept, params)``."""
    session = Session()
    original = session.get
    seen = []

    def get(url, params=None, headers=None, **kwargs):
        if "/documentos/id/" not in url:
            return original(url, params, headers, **kwargs)
        seen.append(((headers or {}).get("Accept"), dict(params or {})))
        return answers(*seen[-1])

    session.get = get
    return session, seen


def test_an_html_acordao_that_fails_as_pdf_is_read_as_the_web_viewer_reads_it():
    html = f"<html><body><p>ACÓRDÃO</p><p>Processo {NUMBER}</p><p>{'Fundamentação. ' * 40}</p></body></html>"

    def answers(accept, params):
        if "pdf" in accept:
            return arq_512()  # TRT20 2G, 2026-11-23: an HTML acórdão asked for as PDF
        return Response(content=json.dumps({"conteudoBase64": base64.b64encode(html.encode()).decode()}).encode())

    session, seen = html_session(answers)
    document = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)["documents"][0]
    assert document["extractionStatus"] == "extracted" and "Fundamentação" in document["text"]
    assert document["content_type"].startswith("text/html") and document["bytes"] == len(html.encode())
    assert "failure" not in document
    assert seen == [
        ("application/pdf,text/html,*/*", {"incluirAssinatura": "false", "incluirCapa": "false"}),
        ("application/json, text/plain, */*", {"incluirAssinatura": "false"}),
    ]


def test_a_document_the_portal_cannot_serve_names_the_portal_error():
    session, seen = html_session(lambda accept, params: arq_512())
    document = process.read_documents(NUMBER, ["9000005"], portal="trt7-1g", session=session)["documents"][0]
    assert document["extractionStatus"] == "http_error"
    assert document["failure"] == "HTTP 500 (ARQ-512: Erro de mapeamento de objetos)"
    assert [params for _, params in seen][1:] == [{"incluirAssinatura": "false"}, {"incluirAssinatura": "true"}]


def test_a_pdf_document_and_a_refusal_take_one_request():
    for answer in (Response(content=b"%PDF-1.4 x", content_type="application/pdf"), Response(403, {"codigoErro": "X"})):
        session, seen = html_session(lambda accept, params, answer=answer: answer)
        read = kz.fetch_document(kz.Api(session, "trt7-1g"), "1000001", "9000005")
        assert len(seen) == 1 and read["status"] in (200, 403)


def test_a_json_answer_without_content_is_not_a_document():
    def answers(accept, params):
        return arq_512() if "pdf" in accept else Response(content=b'{"outro": 1}')

    session, _ = html_session(answers)
    read = kz.fetch_document(kz.Api(session, "trt7-1g"), "1000001", "9000005")
    assert read["content"] is None and "without the document's content" in read["error"]
