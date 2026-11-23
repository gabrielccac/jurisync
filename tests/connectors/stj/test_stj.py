"""STJ CPE connector, offline: read-only guard, A1 login (registration check), parsers and the MCP tools.

Fixtures follow the shapes the CPE answered on 16/11/2026, with invented names and numbers.
"""

import asyncio
import base64
import datetime
import json
import unittest
from unittest import mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID
from mcp.client.client import Client

from jurisync import portals
from jurisync.connectors.stj import client, parsers, server
from jurisync.core.session_pool import SessionPool

REGISTRO = "202600000017"
CNJ = "0000001-23.2025.8.11.0045"
PROCESS = {
    "numeroRegistro": REGISTRO,
    "numeroRegistroFormatado": "2026/0000001-7",
    "classe": {"codigo": "65", "sigla": "AREsp", "nomeClasse": "AGRAVO EM RECURSO ESPECIAL"},
    "numeroProcessoClasse": 1234567,
    "dataRegistroProtocolo": "2026-09-11",
    "dataAutuacao": "2026-10-08",
    "segredoJustica": False,
    "numeroUnico": CNJ.replace("-", "").replace(".", ""),
    "numeroUnicoFormatado": CNJ,
    "origem": {"codigo": "0400", "nome": "TRIBUNAL DE JUSTIÇA DO ESTADO DE MATO GROSSO", "sigla": "TJMT"},
    "processoEletronico": True,
    "orgaoJulgador": {"codigo": None, "nome": None},
    "orgaoProcessante": {"codigo": "S2", "nome": "SEGUNDA SEÇÃO"},
    "destino": {"codigo": "02", "descricao": "CONCLUSÃO AO MINISTRO RELATOR"},
    "status": {"codigo": "06", "descricao": "Distribuído", "tramitando": False},
    "ministroRelator": [
        {"ministro": {"numMinistro": 1, "nomeMinistro": "PRESIDENTE DO STJ"}, "codigoTipoMinistro": "01"}
    ],
    "nomeLocalOrigem": "LUCAS DO RIO VERDE",
    "nome": "AREsp 1234567",
    "nomeCompleto": "AREsp 1234567/MT (2026/0000001-7)",
    "processoAssunto": [{"descAssunto": "Prestação de Serviços", "descricaoCompleta": "DIREITO CIVIL - Prestação"}],
}
PARTIES = [
    {"tipo": "Parte", "nome": "EMPRESA A S/A", "cpfCnpj": "00000000000100", "descricaoTipoParte": "AGRAVANTE",
     "indicadorAutorReu": "A"},
    {"tipo": "Advogado", "nome": "ADVOGADO UM", "codigoOAB": "MT000001A"},
    {"tipo": "Parte", "nome": "PESSOA B", "cpfCnpj": "00000000001", "descricaoTipoParte": "AGRAVADO",
     "indicadorAutorReu": "R"},
    {"tipo": "Parte", "nome": "PESSOA C", "cpfCnpj": "00000000002", "descricaoTipoParte": "AGRAVADO",
     "indicadorAutorReu": "R"},
    {"tipo": "Advogado", "nome": "ADVOGADA DOIS", "codigoOAB": "GO000002"},
]  # fmt: skip
PHASES = [
    {
        "seq": 2,
        "dataHoraFase": "2026-10-09T17:20:40",
        "textoFase": "Conclusos para decisão",
        "descricaoSimplificada": "x",
    },
    {
        "seq": 1,
        "dataHoraFase": "2026-09-11T15:45:20.656",
        "textoFase": "Recebidos os autos",
        "descricaoSimplificada": "y",
    },
]
INDEX = [
    {"tipo": 1, "sequencialPeca": 1, "sequencialDocumento": 900001, "descricaoTipoPeca": "Petição Inicial",
     "dataJuntada": "11/09/2026 15:44:01", "quantidadePagina": 18, "indicadorOrigemPeca": "Origem", "numeroFolhaPeca": 1},
    {"tipo": 1, "sequencialPeca": 2, "sequencialDocumento": 900002, "descricaoTipoPeca": "Decisão",
     "dataJuntada": "12/09/2026 10:00:00", "quantidadePagina": 9, "indicadorOrigemPeca": "Origem", "numeroFolhaPeca": 19},
    {"tipo": None, "sequencialPeca": 2, "sequencialDocumento": 900002, "descricaoTipoPeca": "Decisão",
     "quantidadePagina": 4},
    {"tipo": 2, "sequencialPeca": 0, "seqIndiceProcessoLink": 7, "descricaoTipoPeca": "Link"},
]  # fmt: skip
ROW = {
    "numeroRegistro": REGISTRO, "processo": "AREsp 1234567", "dataAutuacao": "2026-10-22T03:00:00.000+0000",
    "numeroUnico": PROCESS["numeroUnico"], "nomeParteAutora": "EMPRESA A S/A", "nomeParteRe": "PESSOA B",
    "dataUltimaFase": "2026-10-24T01:11:00.000+0000", "textoFase": "Conclusos para decisão", "tramitando": "S",
    "orgaoJulgador": "SEGUNDA SEÇÃO", "segredoJustica": "N", "ministroRelator": "PRESIDENTE DO STJ",
    "assunto": "DIREITO CIVIL", "qtdTotalProcessos": "5", "cpfCnpjParteAutora": "00000000000100",
}  # fmt: skip


PARTIES_LAWYER = [
    {"tipo": "Parte", "seq": 11, "nome": "MUNICIPIO A", "indicadorAutorReu": "A", "seqParteProcesso": 101},
    {"tipo": "Advogado", "seq": 0, "nome": "ADVOGADO UM", "codigoOAB": "BA000001"},
    {"tipo": "Parte", "seq": 22, "nome": "PESSOA B", "indicadorAutorReu": "R", "seqParteProcesso": 102},
    {"tipo": "Parte", "seq": 33, "nome": "PESSOA C", "indicadorAutorReu": "R", "seqParteProcesso": 103},
    {"tipo": "Advogado", "seq": 0, "nome": "ADVOGADA TESTE", "codigoOAB": "BA000002"},
]  # fmt: skip
TRAMITACAO = [{"tramitando": "N", "qtd": 6}, {"tramitando": "S", "qtd": 1}]
VISTA = {"codTipoDocumento": 10, "descTipoDocumento": "Acórdão", "dataVista": "2026-11-10", "cpfParte": "00000000001"}
# One DJEN item as the Comunica API answered for an STJ intimation (names and numbers invented).
DJEN_ITEM = {
    "id": 400000001, "data_disponibilizacao": "2026-11-06", "siglaTribunal": "STJ", "tipoComunicacao": "Intimação",
    "nomeOrgao": "COORDENADORIA DE JULGAMENTO", "idOrgao": 59697, "numero_processo": "00000012320258050000",
    "texto": "<html><body><p>ACÓRDÃO Vistos e relatados estes autos.</p></body></html>", "meio": "D",
    "link": "https://processo.stj.jus.br/processo/julgamento/eletronico/documento/mediado/?registro_numero=202600000017",
    "tipoDocumento": "EMENTA / ACORDÃO", "nomeClasse": "AGRAVO EM RECURSO ESPECIAL", "codigoClasse": "11881",
    "ativo": True, "hash": "abc", "status": "P", "datadisponibilizacao": "06/11/2026",
    "numeroprocessocommascara": "0000012-32.2025.8.05.0000",
    "destinatarios": [{"nome": "1. EMPRESA A S/A (AGRAVANTE)", "polo": "A"}], "destinatarioadvogados": [],
}  # fmt: skip


def jwt(exp):
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS512'})}.{part({'sub': '00000000001', 'exp': exp})}.signature"


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b"", content_type="application/json"):
        self.status_code = status
        self._payload = payload
        self.content = content if payload is None else json.dumps(payload).encode()
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_content(self, chunk_size=1):
        yield self.content

    def close(self):
        self.closed = True


class FakeHttp:
    """Answers by (method, path); records every request, so a test can prove nothing was sent."""

    def __init__(self, routes):
        self.routes = routes
        self.sent = []
        self.headers = {}

    def _answer(self, method, url, **kwargs):
        path = url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        self.sent.append((method, "/" + path, kwargs.get("params"), kwargs.get("json")))
        answer = self.routes.get((method, "/" + path))
        if answer is None:
            return FakeResponse(404, {"status": 404})
        return answer(kwargs) if callable(answer) else answer

    def request(self, method, url, **kwargs):
        return self._answer(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, **kwargs)

    def close(self):
        pass


def session_with(routes, exp=None):
    exp = exp if exp is not None else datetime.datetime.now().timestamp() + 4 * 3600
    return client.StjSession(http=FakeHttp(routes), token=jwt(exp), expires=exp, cpf="00000000001")


READ_ROUTES = {
    ("GET", f"/api/processo/{REGISTRO}"): FakeResponse(payload=PROCESS),
    ("GET", f"/api/processo/{REGISTRO}/partesadvogados"): FakeResponse(payload=PARTIES),
    ("GET", f"/api/processo/{REGISTRO}/faseprocessoext"): FakeResponse(payload=PHASES),
    ("GET", f"/api/processo/{REGISTRO}/indiceprocesso"): FakeResponse(payload=INDEX),
    ("GET", "/api/processo/getNumeroRegistroByNumeroUnico"): FakeResponse(
        payload=[
            {
                "numeroRegistro": "202500000019",
                "siglaClasse": "REsp",
                "numeroProcessoClasse": 7,
                "uf": "MT",
                "numeroUnico": PROCESS["numeroUnico"],
            },
            {
                "numeroRegistro": REGISTRO,
                "siglaClasse": "AREsp",
                "numeroProcessoClasse": 1234567,
                "uf": "MT",
                "numeroUnico": PROCESS["numeroUnico"],
            },
        ]
    ),  # fmt: skip
    ("POST", "/api/advogados/processosFiltro"): FakeResponse(payload=[ROW]),
    ("GET", "/api/advogados/minhasOABs/00000000001"): FakeResponse(
        payload=[{"codigoOAB": "BA000002", "oabPrincipal": "S"}]
    ),
    ("GET", "/api/advogados/BA000002/qtdprocessotramitacao"): FakeResponse(payload=TRAMITACAO),
    ("GET", "/api/advogados/BA000002/processosInteresse"): FakeResponse(payload=[]),
    ("GET", "/api/mensagens/proxima"): FakeResponse(204, content=b""),
}


class ReadOnlyGuardTest(unittest.TestCase):
    WRITES = (
        ("POST", f"/api/cienciaAntecipada/{REGISTRO}/incluirCienciaAntecipada/900002"),
        ("GET", f"/api/cienciaAntecipada/{REGISTRO}/documentos"),
        ("POST", "/api/peticao/incluirPeticaoComDocumentos"),
        ("POST", "/api/sisbajud/incluirPeticaoComDocumentos"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/signup"),
        ("POST", "/api/credenciado/resetPassword"),
        ("POST", "/api/credenciado/resetMfa"),
        ("POST", "/api/credenciado/incluirOAB"),
        ("POST", "/api/interessado/incluirProcesso"),
        ("GET", f"/api/interessado/excluirProcesso/{REGISTRO}"),
        ("POST", "/api/advogados/setProcessoFavorito"),
        ("POST", "/api/advogados/meusProcessos/anotacao"),
        ("GET", "/api/mensagens/lida/1"),
        ("GET", f"/api/processo/{REGISTRO}/solicitarChaveAcesso"),
        ("POST", "/api/peticao/setDataRecebimento"),
        ("PUT", f"/api/processo/{REGISTRO}"),
        ("DELETE", f"/api/processo/{REGISTRO}"),
        ("POST", f"/api/processo/{REGISTRO}"),
        ("GET", "/info"),
        ("GET", "/api/../info"),
    )
    READS = (
        ("GET", f"/api/processo/{REGISTRO}"),
        ("GET", f"/api/processo/{REGISTRO}/indiceprocesso"),
        ("GET", f"/api/pecas/{REGISTRO}/getPecaAutorizacao"),
        ("GET", f"/api/visualizador/externo/processos/{REGISTRO}/peca"),
        ("GET", "/api/advogados/GO000002/publicacoes"),
        ("POST", "/api/advogados/processosFiltro"),
        ("GET", "/api/advogados/BA000002/qtdprocessotramitacao"),
        ("GET", "/api/advogados/BA000002/processosInteresse"),
        ("GET", f"/api/processo/{REGISTRO}/vistasEmAberto"),
        ("GET", "/api/mensagens/proxima"),
    )

    def test_writes_are_refused_before_any_request(self):
        session = session_with({})
        for method, path in self.WRITES:
            with self.subTest(method=method, path=path):
                with self.assertRaises(PermissionError):
                    client.api(session, method, path)
        self.assertEqual(session.http.sent, [])

    def test_reads_pass(self):
        for method, path in self.READS:
            with self.subTest(method=method, path=path):
                client.require_read(method, path)

    def test_expired_token_and_denied_process(self):
        session = session_with(
            {("GET", "/api/processo/1"): FakeResponse(401, {}), ("GET", "/api/processo/2"): FakeResponse(403, {})}
        )
        with self.assertRaises(client.StjSessionExpired):
            client.api(session, "GET", "/api/processo/1")
        with self.assertRaises(client.StjAccessDenied):
            client.api(session, "GET", "/api/processo/2")


def a1(common_name, pin="pin"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(b"a1", key, cert, None, BestAvailableEncryption(pin.encode()))


@pytest.fixture(scope="module")
def e_cpf(tmp_path_factory):
    path = tmp_path_factory.mktemp("a1") / "a1.p12"
    path.write_bytes(a1("ADVOGADA TESTE:00000000001"))
    return str(path)


def login_routes(registered=True, answer=None):
    return {
        ("GET", "/api/credenciado/isCredenciado/00000000001"): FakeResponse(payload=registered),
        ("POST", "/api/auth/cert"): FakeResponse(payload=answer),
    }


def test_login_sets_the_token_header_and_its_expiry(e_cpf):
    token = jwt(1_900_000_000)
    http = FakeHttp(login_routes(answer={"username": "00000000001", "token": token, "novoCredenciado": False}))
    session = client.login(pin="pin", p12_path=e_cpf, http=http)
    assert http.headers["authorization"] == token
    assert session.expires == 1_900_000_000
    assert session.cpf == "00000000001"
    assert [request[:2] for request in http.sent] == [
        ("GET", "/api/credenciado/isCredenciado/00000000001"),
        ("POST", "/api/auth/cert"),
    ]


def test_each_office_logs_in_with_its_own_a1_and_keeps_its_own_token(e_cpf, monkeypatch):
    from jurisync.core import credentials

    offices = {
        tenant: (a1(f"ADVOGADA {tenant.upper()}:{cpf}", pin=f"pin-{tenant}"), cpf)
        for tenant, cpf in (("office-a", "00000000002"), ("office-b", "00000000003"))
    }
    made = []

    def login(_key):
        cpf = offices[credentials.tenant()][1]
        token = jwt(1_900_000_000 + len(made))
        http = FakeHttp(
            {
                ("GET", f"/api/credenciado/isCredenciado/{cpf}"): FakeResponse(payload=True),
                ("POST", "/api/auth/cert"): FakeResponse(payload={"username": cpf, "token": token}),
            }
        )
        made.append((credentials.tenant(), http))
        return client.login(http=http)

    # The operator's own A1 in the server's environment is never used for an office.
    monkeypatch.setenv("STJ_A1_P12", e_cpf)
    monkeypatch.setenv("STJ_A1_PIN", "pin")
    monkeypatch.setattr(server.sessions, "_login", login)
    seen = {}
    try:
        for tenant in ("office-a", "office-b", "office-a"):
            p12, _ = offices[tenant]
            with credentials.scope(tenant, {"STJ_A1_PIN": f"pin-{tenant}"}, {"STJ_A1_P12": p12}):
                seen.setdefault(tenant, []).append(server.sessions.run("stj", lambda session: session))
    finally:
        server.sessions.close()
    assert seen["office-a"][0] is seen["office-a"][1]
    assert [tenant for tenant, _ in made] == ["office-a", "office-b"]
    assert seen["office-a"][0].token != seen["office-b"][0].token
    for tenant, http in made:
        cpf = offices[tenant][1]
        assert [request[:2] for request in http.sent] == [
            ("GET", f"/api/credenciado/isCredenciado/{cpf}"),
            ("POST", "/api/auth/cert"),
        ]
        assert seen[tenant][0].cpf == cpf
        assert http.headers["authorization"] == seen[tenant][0].token


def test_an_unregistered_lawyer_never_reaches_the_certificate_login(e_cpf):
    http = FakeHttp(login_routes(registered=False))
    with pytest.raises(client.StjNotRegistered, match="never registers"):
        client.login(pin="pin", p12_path=e_cpf, http=http)
    assert [request[0] for request in http.sent] == ["GET"]


def test_a_new_registration_answer_stops_the_login(e_cpf):
    http = FakeHttp(login_routes(answer={"token": jwt(1), "novoCredenciado": True}))
    with pytest.raises(client.StjNotRegistered):
        client.login(pin="pin", p12_path=e_cpf, http=http)
    assert "authorization" not in http.headers


def test_a_certificate_without_cpf_is_refused(tmp_path):
    path = tmp_path / "a1.p12"
    path.write_bytes(a1("EMPRESA SEM CPF"))
    http = FakeHttp(login_routes())
    with pytest.raises(RuntimeError, match="e-CPF"):
        client.login(pin="pin", p12_path=str(path), http=http)
    assert http.sent == []


def test_the_pool_logs_in_again_before_the_token_expires():
    session = session_with({}, exp=10_000)
    assert client.refresh(session, now=10_000 - client.RENEW_BEFORE - 1) is session
    assert client.refresh(session, now=10_000 - client.RENEW_BEFORE + 1) is None
    assert client.token_expiry("not-a-jwt", now=50) == 3650


class ParserTest(unittest.TestCase):
    def test_process_references(self):
        cases = {
            CNJ: ("cnj", "00000012320258110045"),
            "00000012320258110045": ("cnj", "00000012320258110045"),
            "2026/0000001-7": ("registro", REGISTRO),
            REGISTRO: ("registro", REGISTRO),
            "AREsp 1234567": ("classe", ("AREsp", "1234567")),
            "REsp 1234567/SP": ("classe", ("REsp", "1234567")),
            "AgInt no AREsp 99": ("classe", ("AgIntnoAREsp", "99")),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parsers.classify(value), expected)
        with self.assertRaises(ValueError):
            parsers.classify("../../api/auth")

    def test_cover_keeps_what_a_lawyer_reads(self):
        cover = parsers.cover(PROCESS)
        self.assertEqual(cover["registro"], "2026/0000001-7")
        self.assertEqual(cover["cnj"], CNJ)
        self.assertEqual(cover["court"], "SEGUNDA SEÇÃO")
        self.assertEqual(cover["rapporteurs"], ["PRESIDENTE DO STJ"])
        self.assertEqual(cover["origin"]["abbreviation"], "TJMT")
        self.assertEqual(cover["status"], "Distribuído")

    def test_lawyers_go_to_the_run_of_parties_before_them_and_no_document_numbers_leak(self):
        groups = parsers.party_groups(PARTIES)
        self.assertEqual([(g["name"], [lawyer["oab"] for lawyer in g["lawyers"]]) for g in groups], [
            ("EMPRESA A S/A", ["MT000001A"]),
            ("PESSOA B", ["GO000002"]),
            ("PESSOA C", ["GO000002"]),
        ])  # fmt: skip
        self.assertNotIn("0000000", json.dumps(groups))

    def test_index_has_one_entry_per_piece_and_skips_links(self):
        pieces = parsers.pieces(INDEX)
        self.assertEqual(
            [(p["id"], p["type"], p["pages"]) for p in pieces], [("1", "Petição Inicial", 18), ("2", "Decisão", 13)]
        )

    def test_process_row_drops_party_documents(self):
        row = parsers.process_row(ROW)
        self.assertEqual((row["registro"], row["cnj"], row["active"]), ("2026/0000001-7", CNJ, True))
        self.assertNotIn("00000000000100", json.dumps(row))


class CounterParsersTest(unittest.TestCase):
    def test_tramitacao(self):
        self.assertEqual(parsers.tramitacao(TRAMITACAO), {"active": 1, "closed": 6, "total": 7})
        self.assertEqual(parsers.tramitacao(None), {"active": 0, "closed": 0, "total": 0})

    def test_represented_seqs_follow_the_lawyer_runs(self):
        self.assertEqual(parsers.represented_seqs(PARTIES_LAWYER, "BA000002"), [22, 33])
        self.assertEqual(parsers.represented_seqs(PARTIES_LAWYER, "BA000001"), [11])
        self.assertEqual(parsers.represented_seqs(PARTIES_LAWYER, "BA999999"), [])

    def test_vista_keeps_plain_fields_but_no_cpf(self):
        row = parsers.vista(VISTA)
        self.assertEqual(row["documentType"], 10)
        self.assertEqual(row["fields"]["dataVista"], "2026-11-10")
        self.assertNotIn("cpfParte", row["fields"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        routes = dict(READ_ROUTES)
        routes[("GET", f"/api/pecas/{REGISTRO}/getPecaAutorizacao")] = lambda kwargs: FakeResponse(
            payload={"access_token": "grant-" + str(kwargs["params"]["seq"]), "csrf": "c"}
        )
        html = ("<html><body>" + "Petição inicial do agravante. " * 40 + "</body></html>").encode()
        routes[("GET", f"/api/visualizador/externo/processos/{REGISTRO}/peca")] = FakeResponse(
            content=html, content_type="text/html"
        )
        self.session = session_with(routes)
        pool = SessionPool(login=lambda _key: self.session, refresh=lambda _key, s: s, close=lambda s: None)
        patcher = mock.patch.object(server, "sessions", pool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, arguments):
        async def run():
            async with Client(server.create_server()) as mcp_client:
                return await mcp_client.call_tool(name, arguments)

        result = asyncio.run(run())
        if result.is_error:
            return result.content[0].text, True
        return result.structured_content or json.loads(result.content[0].text), False

    def test_tools_and_annotations(self):
        async def tools():
            async with Client(server.create_server()) as mcp_client:
                return {tool.name: tool for tool in (await mcp_client.list_tools()).tools}

        listed = asyncio.run(tools())
        self.assertEqual(
            sorted(listed),
            ["stj_download_document", "stj_get_counters", "stj_get_process", "stj_list_inbox", "stj_list_processes",
             "stj_list_publications", "stj_read_documents"],
        )  # fmt: skip
        for tool in listed.values():
            self.assertEqual(tool.annotations.read_only_hint, tool.name != "stj_download_document")
            self.assertFalse(tool.annotations.destructive_hint)

    def test_get_process_by_cnj_picks_the_latest_record(self):
        data, error = self.call("stj_get_process", {"process": CNJ})
        self.assertFalse(error)
        self.assertEqual(data["registro"], "2026/0000001-7")
        self.assertEqual(data["relatedRecords"][0]["process"], "REsp 7")
        self.assertEqual([p["id"] for p in data["documents"]], ["1", "2"])
        self.assertEqual(data["phases"][0]["text"], "Conclusos para decisão")

    def test_get_process_by_class_and_number_sends_the_class_upper_cased(self):
        self.session.http.routes[("GET", "/api/processo/getNumeroRegistroBySiglaClasseNumero")] = FakeResponse(
            payload=REGISTRO
        )
        data, error = self.call("stj_get_process", {"process": "AREsp 1234567", "include_documents": False})
        self.assertFalse(error)
        self.assertEqual(data["registro"], "2026/0000001-7")
        lookup = [r for r in self.session.http.sent if r[1].endswith("BySiglaClasseNumero")][0]
        self.assertEqual(lookup[2], {"siglaClasse": "ARESP", "numeroClasse": "1234567"})

    def test_read_documents_only_reads_pieces_of_the_index(self):
        data, error = self.call("stj_read_documents", {"process": "2026/0000001-7", "document_ids": ["1"]})
        self.assertFalse(error)
        self.assertIn("Petição inicial", data["documents"][0]["text"])
        self.assertEqual(data["ciencia"], {"estado": "nenhuma"})
        self.assertNotIn("grant-", json.dumps(data))
        grant = [r for r in self.session.http.sent if r[1].endswith("getPecaAutorizacao")]
        self.assertEqual(grant[0][2]["seq"], "1")
        message, error = self.call("stj_read_documents", {"process": REGISTRO, "document_ids": ["99"]})
        self.assertTrue(error)
        self.assertIn("not in this process", message)

    def test_list_processes(self):
        data, error = self.call("stj_list_processes", {"status": "active"})
        self.assertFalse(error)
        self.assertEqual(data["total"], 5)
        body = [r for r in self.session.http.sent if r[0] == "POST"][0][3]
        self.assertEqual(body, {"page": 0, "qtd": "20", "tramitando": "S"})

    def vista_routes(self, vistas):
        self.session.http.routes[("GET", f"/api/processo/{REGISTRO}/partesadvogados")] = FakeResponse(
            payload=PARTIES_LAWYER
        )
        self.session.http.routes[("GET", f"/api/processo/{REGISTRO}/vistasEmAberto")] = FakeResponse(payload=vistas)

    def test_counters(self):
        self.vista_routes([VISTA])
        data, error = self.call("stj_get_counters", {})
        self.assertFalse(error)
        self.assertEqual(data["oabs"], [{"oab": "BA000002", "active": 1, "closed": 6, "total": 7}])
        self.assertEqual((data["active"], data["closed"], data["total"]), (1, 6, 7))
        self.assertEqual((data["processesWithOpenVista"], data["processesOfInterest"]), (1, 0))
        self.assertFalse(data["unreadPortalMessage"])
        vistas = [r for r in self.session.http.sent if r[1].endswith("vistasEmAberto")][0]
        self.assertEqual(vistas[2], {"values": "22,33"})  # only the parties of BA000002

    def test_inbox_lists_vistas_and_djen_notices(self):
        self.vista_routes([VISTA])
        with mock.patch.object(server.djen, "fetch_page", return_value={"count": 1, "items": [DJEN_ITEM]}) as fetch:
            data, error = self.call("stj_list_inbox", {"days": 10, "include_text": True})
        self.assertFalse(error)
        self.assertEqual(fetch.call_args.args[:2], ("2", "BA"))
        self.assertEqual(fetch.call_args.args[4], "STJ")
        self.assertEqual(data["vistas"][0]["registro"], "2026/0000001-7")
        self.assertEqual(data["vistas"][0]["vistas"][0]["documentType"], 10)
        self.assertNotIn("cpfParte", json.dumps(data))
        self.assertEqual(data["activeProcesses"][0]["lastPhase"], "Conclusos para decisão")
        notice = data["notices"][0]
        self.assertEqual(notice["numero_processo_mascara"], "0000012-32.2025.8.05.0000")
        self.assertIn("Vistos e relatados", notice["texto"])
        self.assertEqual(data["noticesTotal"], 1)

    def test_inbox_without_vistas_or_text(self):
        self.vista_routes([])
        with mock.patch.object(server.djen, "fetch_page", return_value={"count": 0, "items": []}):
            data, error = self.call("stj_list_inbox", {})
        self.assertFalse(error)
        self.assertEqual((data["vistas"], data["notices"]), ([], []))

    def test_no_tool_ever_sends_a_write(self):
        checked, refused = [], []
        guard = client.require_read

        def spy(method, path):
            checked.append((method, path))
            try:
                guard(method, path)
            except PermissionError:
                refused.append((method, path))
                raise

        self.vista_routes([VISTA])
        with mock.patch.object(client, "require_read", side_effect=spy):
            with mock.patch.object(server.djen, "fetch_page", return_value={"count": 0, "items": []}):
                results = {"stj_list_inbox": self.call("stj_list_inbox", {})}
            for name, arguments in (
                ("stj_get_counters", {}),
                ("stj_get_process", {"process": CNJ}),
                ("stj_read_documents", {"process": REGISTRO, "document_ids": ["1", "2"]}),
                ("stj_list_processes", {}),
            ):
                results[name] = self.call(name, arguments)
        for name, (data, error) in results.items():
            self.assertFalse(error, f"{name}: {data}")
        self.assertEqual(refused, [])
        sent = [(method, path) for method, path, _params, _body in self.session.http.sent]
        self.assertTrue(sent)
        # Every request on the wire went through the guard first, and none of them is a write.
        self.assertLessEqual(set(sent), set(checked))
        for method, path in sent:
            self.assertTrue(method == "GET" or path in client.READ_POSTS, (method, path))
            self.assertNotIn("ciencia", path.lower())


def test_configured_portals_needs_the_certificate_and_its_pin():
    with mock.patch.dict("os.environ", {"PJE_A1_P12": "/tmp/a1.p12"}, clear=True):
        assert server.configured_portals() == ()
    with mock.patch.dict("os.environ", {"PJE_A1_P12": "/tmp/a1.p12", "PJE_A1_PIN": "pin"}, clear=True):
        assert server.configured_portals() == ("stj",)
    with mock.patch.dict("os.environ", {"STJ_A1_P12": "/tmp/stj.p12", "STJ_A1_PIN": "pin"}, clear=True):
        assert server.configured_portals() == ("stj",)


def test_the_portal_is_wired_and_claims_every_stj_link():
    config = portals.stj("stj")
    assert config.auth == "certificate_mtls"
    for host in ("processo.stj.jus.br", "ww2.stj.jus.br", "cpe.web.stj.jus.br"):
        assert portals.for_host(host).key == "stj"
