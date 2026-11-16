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
            ["stj_download_document", "stj_get_process", "stj_list_processes", "stj_list_publications",
             "stj_read_documents"],
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

    def test_read_documents_only_reads_pieces_of_the_index(self):
        data, error = self.call("stj_read_documents", {"process": "2026/0000001-7", "document_ids": ["1"]})
        self.assertFalse(error)
        self.assertIn("Petição inicial", data["documents"][0]["text"])
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

    def test_no_tool_ever_sends_a_write(self):
        for name, arguments in (
            ("stj_get_process", {"process": CNJ}),
            ("stj_read_documents", {"process": REGISTRO, "document_ids": ["1", "2"]}),
            ("stj_list_processes", {}),
        ):
            self.call(name, arguments)
        for method, path, _params, _body in self.session.http.sent:
            client.require_read(method, path)
            self.assertNotIn("ciencia", path.lower())


def test_the_portal_is_wired_and_claims_every_stj_link():
    config = portals.stj("stj")
    assert config.auth == "certificate_mtls"
    for host in ("processo.stj.jus.br", "ww2.stj.jus.br", "cpe.web.stj.jus.br"):
        assert portals.for_host(host).key == "stj"
