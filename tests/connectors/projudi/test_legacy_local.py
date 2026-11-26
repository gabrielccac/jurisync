"""Projudi legacy-local adapter (TJAM) against synthetic pages with the structure observed live on 2026-11-16.

Login page, post-login frameset menu (``add_menu``), desk, search form, process page (``tr[id^=mov]`` rows and the
``movimentacaoArquivoDocumento`` scripts), parties tab, movement file list and intimação agenda keep the markup of the
real pages; every name, number and token is synthetic.
"""

import dataclasses
import hashlib
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from jurisync import portals
from jurisync.connectors.projudi import base, legacy, server, tjam
from jurisync.core.http import UnsafeUrlError
from jurisync.core.session_pool import LoginRejected

KEY = "tjam-1g"
APP = "https://projudi.tjam.jus.br/projudi/"
ENTRY = APP + "usuario/logon.do?actionType=inicio"
ACTION = APP + "usuario/logon.do;jsessionid=ABC123?_tj=tok1"
SALT = "ab" * 32
LOGIN = "AM123456"  # synthetic UF000-style login
PASSWORD = "synthetic-pass"
NUMBER = "0000001-23.2026.8.04.0001"
ENV = {"TJAM_1G_USERNAME": LOGIN, "TJAM_1G_PASSWORD": PASSWORD}

LOGIN_PAGE = f"""<html><head><script src="/TSPD/?type=18"></script></head><body>
<form method="post" action="/projudi/usuario/logon.do;jsessionid=ABC123?_tj=tok1" name="formLogin" id="formLogin" onsubmit="return submitLogin()">
<input type="hidden" id="latitude" name="latitude"><input type="hidden" id="longitude" name="longitude">
<input type="hidden" id="locationGranted" name="locationGranted">
<input type="text" id="login" name="login" maxlength="30"><input type="password" id="senha" name="senha" maxlength="30">
<input type="submit" id="btEntrar" value="Entrar"><input type="hidden" name="tck" id="tck" value="principal"/></form>
<!-- Recaptcha
<div class="g-recaptcha" data-sitekey="x"></div>
-->
<script>function submitLogin(){{ document.formLogin.btEntrar.disabled=true; var login =document.formLogin.login.value;
var key = hex_md5("{SALT}" + login); document.formLogin.tck.value=key; document.formLogin.submit(); return false; }}</script>
</body></html>"""
LOGGED_IN = (
    '<html><head><script>function openDialogSessaoExpirada() { confirm("Sua sessão expirou."); }</script></head>'
    '<body><a href="/projudi/usuario/logon.do?actionType=logout&forcarLogout=true">Sair</a></body></html>'
)
DESK = APP + "usuario/mesaAdvogado.do?actionType=listaInicio"
TJ = "ab12" * 8
POST_LOGON = f"""<html><script>add_menu("Buscas");
add_menu("Processos 1º Grau", "/processo/buscaProcessosQualquerInstancia.do?_tj={TJ}", "userMainFrame", "Busca");
add_menu("Intimações", "/processo/ciencia.do?_tj={TJ}", "userMainFrame", "");</script>
<frameset><frame src="/projudi/usuario/mesaAdvogado.do?actionType=listaInicio"></frameset></html>"""
SEARCH_PAGE = """<html><a href="/projudi/usuario/logon.do?actionType=logout&forcarLogout=true">Sair</a>
<script>confirm('Sua sessão expirou. Clique em OK');</script>
<form name="buscaProcessosQualquerInstanciaForm" method="post"
 action="/projudi/processo/buscaProcessosQualquerInstancia.do?actionType=pesquisar">
<input type="hidden" name="instanciaSuperior" value="false"><input type="hidden" name="page" value="1">
<input type="radio" name="filtroAdvogado" value="restritoAdvogado" checked><input type="text" name="numeroProcesso">
<input type="radio" name="flagNumeroProcessoUnico" value="true" checked></form></html>"""
SEARCH_RESULT = f"""<html><table><tr><td><a href="/projudi/processo.do?_tj={TJ}cd">{NUMBER}</a></td>
<td>Autor: Pessoa Exemplo</td></tr></table></html>"""
FILES_URL = f"/projudi/processo/movimentacaoArquivoDocumento.do?_tj={TJ}ef"
PROCESS_PAGE = f"""<html><div id="barraTituloStatusProcessual">Processo {NUMBER}</div>
<table class="form"><tr><td class="label">Classe Processual:</td><td>1116 - Execução Fiscal</td></tr>
<tr><td class="label">Nível de Sigilo:</td><td>Público</td></tr></table>
<form id="processoForm" name="processoForm" method="post" action="/projudi/visualizacaoProcesso.do?actionType=visualizar&id=1">
<input type="hidden" name="id" value="1"><input type="hidden" name="selectedIcon" value=""></form>
<table><tr class="odd" id="mov1Grau,SERVIDOR,,,,"><td></td><td>2</td><td>06/11/2026 12:38:17</td>
<td>EXPEDIÇÃO DE INTIMAÇÃO</td><td>Servidor Exemplo</td></tr>
<tr class="even" id="mov1Grau,SERVIDOR,,,,"><td><a href="javascript://nop/" class="linkArquivosmovimentacoes1"><img></a>
<script type="text/javascript">var aj_null = new AjaxJspTag.HtmlContent(
"{FILES_URL}", {{
sourceClass: "linkArquivosmovimentacoes1",
target: "divArquivosMovimentacaoProcessomovimentacoes1"}});</script></td>
<td>1</td><td>05/11/2026 10:35:16</td><td><b>JUNTADA DE PETIÇÃO</b></td><td>Advogado Exemplo</td></tr>
<tr id="rowmovimentacoes1" style="display:none"><td colspan="5"><div></div></td></tr></table></html>"""
PARTIES_PAGE = """<html><div id="tabprefix2"><h4>Exequente</h4><table class="resultTable"><thead><tr><th></th>
<th>Nome</th></tr></thead><tbody><tr class="even"><td><img id="iconpromoventes0"></td><td><a>Parte Autora</a></td>
<td></td><td>00.000.000/0001-00</td><td></td><td>OAB 1N-AM - ADVOGADA EXEMPLO</td></tr>
<tr id="rowpromoventes0"><td colspan="6">Logradouro: Rua Exemplo</td></tr></tbody></table>
<h4>Executado</h4><table class="resultTable"><tbody><tr class="odd"><td><img id="iconpromovidas0"></td>
<td><a>Parte Ré</a></td><td></td><td>000.000.000-00</td><td></td><td> Parte sem advogado </td></tr></tbody></table>
</div></html>"""
FILES_PAGE = f"""<table class="form"><tbody><tr><td width="34%"> 1.1 Arquivo: Petição exemplo </td><td>&nbsp;</td>
<td>Ass.: ADVOGADO EXEMPLO</td><td>&nbsp;</td><td><a onclick="x();" target="_blank"
 href="/projudi/arquivo.do?_tj={TJ}99" class="link">peticao.pdf</a></td><td>&nbsp;</td><td>Segredo</td></tr></tbody></table>"""
AGENDA_EMPTY = """<html><div id="tabprefix1">Agenda de Intimações 0 registro(s) encontrado(s), exibindo de 1 até 0
Nenhum registro encontrado</div></html>"""
AGENDA_PENDING = f"""<html><div id="tabprefix1">Agenda de Intimações 1 registro(s) encontrado(s), exibindo de 1 até 1
<table><tr><td>{NUMBER}</td><td>Parte Autora</td></tr></table></div></html>"""
REJECTED = "<html><body>Alguns erros foram encontrados: Usuário ou senha inválidos!" + LOGIN_PAGE + "</body></html>"
WAF = "<html><body>The requested URL was rejected. Request Rejected. Your support ID is: 1</body></html>"
FRAMESET = '<html><frameset><frame name="mainFrame" src="paginaPrincipal.jsp"></frameset></html>'
MAIN = f"""<html><body><a href="{APP}processo/busca.do;jsessionid=ABC?_tj=a1">Busca Processual</a>
<a href="{APP}processo/ciencia.do?_tj=b2">Ciência</a><a href="{APP}usuario/logon.do?actionType=logout">Sair</a></body></html>"""
SEARCH = f"""<html><form action="{APP}processo/busca.do?_tj=c3" method="post"><input type="hidden" name="h" value="1">
<input type="text" name="numeroProcesso"></form></html>"""
RESULT = f"<html><h2>{NUMBER}</h2><table><tr><td>03/01/2026</td><td>Despacho sintético</td></tr></table></html>"


def response(status=200, text="", location="", url=""):
    headers = {"Location": location} if location else {}
    content = text.encode("iso-8859-1")
    return SimpleNamespace(
        status_code=status,
        headers=headers,
        text=text,
        content=content,
        url=url,
        iter_content=lambda chunk_size=None: iter([content]),
        close=lambda: None,
    )


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        result = self.routes(method, url, kwargs)
        result.url = result.url or url
        return result

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._answer(method, url, kwargs)

    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


def login_routes(*, entry_page=LOGIN_PAGE, post_page=LOGGED_IN, post_status=200):
    def routes(method, url, kwargs):
        if url == ENTRY:
            return response(200, entry_page)
        if url == ACTION:
            return response(post_status, post_page)
        if url == DESK:
            return response(200, LOGGED_IN)
        raise AssertionError(f"unexpected {method} {url}")

    return routes


def wired(test):
    patcher = mock.patch.dict(portals.PORTALS, {KEY: dataclasses.replace(portals.PORTALS[KEY], wired=True)})
    env = mock.patch.dict(os.environ, ENV)
    test.addCleanup(patcher.stop)
    test.addCleanup(env.stop)
    patcher.start()
    env.start()


class RegistryTest(unittest.TestCase):
    def test_tjam_is_wired_and_registered(self):
        self.assertTrue(portals.PORTALS[KEY].wired)
        self.assertIs(server.ADAPTERS["legacy-local"], tjam.ADAPTER)
        self.assertEqual(portals.PORTALS[KEY].login_url, ENTRY)
        self.assertFalse(portals.PORTALS[KEY].guess_by_number)

    def test_there_is_no_certificate_login(self):
        self.assertNotIn("certificate", tjam.TjamAdapter.login.__code__.co_names)
        self.assertEqual(portals.PORTALS[KEY].auth, "password")


class LoginTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_tck_is_the_md5_of_salt_and_login_and_the_post_has_no_button(self):
        session = FakeSession(login_routes())
        self.assertIs(tjam.ADAPTER.login(KEY, session=session), session)
        (post,) = session.posts()
        self.assertEqual(post[1], ACTION)  # the page's action, ;jsessionid and _tj included, verbatim
        self.assertEqual(
            post[2]["data"],
            {
                "latitude": "",
                "longitude": "",
                "locationGranted": "false",
                "tck": hashlib.md5((SALT + LOGIN).encode(), usedforsecurity=False).hexdigest(),
                "login": LOGIN,
                "senha": PASSWORD,
            },
        )
        self.assertNotIn("btEntrar", post[2]["data"])

    def test_the_commented_out_recaptcha_is_not_a_challenge(self):
        self.assertFalse(legacy.has_human_verification(LOGIN_PAGE))
        self.assertTrue(legacy.has_human_verification(WAF))

    def test_cpf_with_punctuation_is_sent_as_digits(self):
        with mock.patch.dict(os.environ, {"TJAM_1G_USERNAME": "529.982.247-25"}):
            self.assertEqual(tjam.login_credentials(portals.PORTALS[KEY])[0], "52998224725")

    def test_missing_credentials_send_nothing(self):
        for name in ("TJAM_1G_USERNAME", "TJAM_1G_PASSWORD"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: ""}):
                session = FakeSession(login_routes())
                with self.assertRaisesRegex(legacy.MissingCredentials, "precisa de login e senha da cliente"):
                    tjam.ADAPTER.login(KEY, session=session)
                self.assertEqual(session.calls, [])

    def test_wrong_password_is_one_post_and_never_retried(self):
        session = FakeSession(login_routes(post_page=REJECTED))
        with self.assertRaises(LoginRejected):
            tjam.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(session.posts()), 1)

    def test_a_waf_page_on_the_login_or_after_the_post_stops_for_a_person(self):
        for routes in (login_routes(entry_page=WAF), login_routes(post_page=WAF)):
            session = FakeSession(routes)
            with self.assertRaisesRegex(legacy.HumanVerificationRequired, "precisa de uma pessoa"):
                tjam.ADAPTER.login(KEY, session=session)
        self.assertEqual(len(FakeSession(login_routes(entry_page=WAF)).posts()), 0)

    def test_a_page_without_the_form_or_the_salt_fails_loudly_without_posting(self):
        for page in ("<html>sem formulário</html>", LOGIN_PAGE.replace(SALT, "")):
            session = FakeSession(login_routes(entry_page=page))
            with self.assertRaises(base.ProjudiParseError):
                tjam.ADAPTER.login(KEY, session=session)
            self.assertEqual(session.posts(), [])

    def test_a_form_that_posts_off_the_login_path_is_refused(self):
        evil = LOGIN_PAGE.replace("/projudi/usuario/logon.do;jsessionid=ABC123?_tj=tok1", "https://evil.invalid/steal")
        with self.assertRaises(UnsafeUrlError):
            tjam.ADAPTER.login(KEY, session=FakeSession(login_routes(entry_page=evil)))

    def test_a_login_that_does_not_reach_a_logged_in_page_is_an_error_not_a_success(self):
        def routes(method, url, kwargs):
            if url == DESK:
                return response(200, LOGIN_PAGE)
            return login_routes()(method, url, kwargs)

        with self.assertRaisesRegex(RuntimeError, "authenticated page"):
            tjam.ADAPTER.login(KEY, session=FakeSession(routes))


class SessionAndReadsTest(unittest.TestCase):
    def setUp(self):
        wired(self)

    def test_session_expired_sentinel_and_login_redirects(self):
        session = FakeSession(lambda *_: response(200, "<html><title>A sessao expirou</title></html>"))
        with self.assertRaises(base.ProjudiSessionExpired):
            tjam.ADAPTER.request(session, KEY, "GET", "usuario/mesaAdvogado.do")
        redirect = response(302, location=APP + "usuario/logon.do?actionType=inicio", url=APP + "x.do")
        self.assertTrue(tjam.ADAPTER.is_login_redirect(redirect))
        self.assertFalse(tjam.ADAPTER.is_login_redirect(response(302, location=APP + "y.do", url=APP + "x.do")))

    def test_the_timeout_dialog_script_of_a_logged_in_page_is_not_an_expired_session(self):
        # Live 2026-11-16: every logged-in page carries "Sua sessão expirou" in a script; reading it as an expiry
        # made the pool log in again.
        self.assertFalse(legacy.session_expired(LOGGED_IN))
        self.assertFalse(legacy.session_expired(SEARCH_PAGE))
        self.assertTrue(legacy.session_expired("<p>A sessão expirou</p>"))
        response_ = tjam.ADAPTER.request(FakeSession(lambda *_: response(200, SEARCH_PAGE)), KEY, "GET", "x.do")
        self.assertEqual(response_.status_code, 200)

    def routes(self, agenda=AGENDA_EMPTY):
        def routes(method, url, kwargs):
            pages = {
                ("GET", APP + "usuario/postLogon.do?actionType=iniciar"): POST_LOGON,
                ("GET", APP + f"processo/buscaProcessosQualquerInstancia.do?_tj={TJ}"): SEARCH_PAGE,
                ("POST", APP + "processo/buscaProcessosQualquerInstancia.do?actionType=pesquisar"): SEARCH_RESULT,
                ("GET", APP + f"processo.do?_tj={TJ}cd"): PROCESS_PAGE,
                ("POST", APP + "visualizacaoProcesso.do?actionType=visualizar&id=1"): PARTIES_PAGE,
                ("POST", APP + FILES_URL.removeprefix("/projudi/")): FILES_PAGE,
                ("POST", DESK): agenda,
            }
            if (method, url) in pages:
                return response(200, pages[(method, url)])
            if (method, url) == ("GET", APP + f"arquivo.do?_tj={TJ}99"):
                return response(200, "%PDF-1.4 synthetic")
            raise AssertionError(f"unexpected {method} {url}")

        return routes

    def test_open_process_searches_by_digits_and_reads_movements_and_parties(self):
        session = FakeSession(self.routes())
        process = tjam.ADAPTER.open_process(session, KEY, NUMBER)
        search = [call for call in session.calls if "pesquisar" in call[1]][0][2]["data"]
        self.assertEqual(search["numeroProcesso"], "00000012320268040001")
        self.assertEqual(search["filtroAdvogado"], "qualquerAdvogado")
        self.assertEqual(
            process["fields"], {"Classe Processual": "1116 - Execução Fiscal", "Nível de Sigilo": "Público"}
        )
        self.assertEqual([m["movementId"] for m in process["movements"]], ["2", "1"])
        self.assertEqual(process["movements"][1]["date"], "05/11/2026")
        self.assertEqual([m["hasFiles"] for m in process["movements"]], [False, True])
        self.assertEqual(
            process["parties"],
            [
                {"role": "Exequente", "name": "Parte Autora", "lawyers": "OAB 1N-AM - ADVOGADA EXEMPLO"},
                {"role": "Executado", "name": "Parte Ré", "lawyers": ""},
            ],
        )
        self.assertNotIn("00.000.000/0001-00", str(process))
        self.assertFalse(any("ciencia" in call[1] or "logout" in call[1] for call in session.calls))

    def test_index_files_and_a_download(self):
        session = FakeSession(self.routes())
        process, index = base.load_process(tjam.ADAPTER, session, KEY, NUMBER)
        (file,) = index[0]["files"]
        self.assertEqual((file["id"], file["label"], file["name"]), ("1001", "1.1", "peticao.pdf"))
        self.assertEqual(process["movements"][1]["files"][0]["id"], "1001")
        content, _ = tjam.ADAPTER.download(session, KEY, "1001", file["hash"])
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_a_pending_intimacao_opens_only_when_acknowledged(self):
        session = FakeSession(self.routes(agenda=AGENDA_PENDING))
        with self.assertRaises(base.CienciaPending) as refused:
            base.read_documents(tjam.ADAPTER, session, KEY, NUMBER, ["1001"])
        self.assertEqual(refused.exception.ciencia["estado"], "pendente")
        self.assertIn("acknowledgeCiencia=true", str(refused.exception))
        self.assertFalse(any("arquivo.do" in call[1] or "pesquisar" in call[1] for call in session.calls))
        _, documents, ciencia = base.read_documents(
            tjam.ADAPTER, session, KEY, NUMBER, ["1001"], acknowledge_ciencia=True
        )
        self.assertEqual((documents[0]["format"], ciencia["estado"]), ("pdf", "dada"))
        self.assertEqual(ciencia["intimacoes"][0]["process"], NUMBER)
        with self.assertRaises(ValueError):
            tjam.ADAPTER.download_url(KEY, "1001", "../evil")

    def test_an_unreadable_agenda_is_unchecked_and_never_a_dead_end(self):
        session = FakeSession(self.routes(agenda="<html>outra página</html>"))
        with self.assertRaises(base.CienciaUnchecked):
            base.read_documents(tjam.ADAPTER, session, KEY, NUMBER, ["1001"])
        _, documents, ciencia = base.read_documents(
            tjam.ADAPTER, session, KEY, NUMBER, ["1001"], acknowledge_ciencia=True
        )
        self.assertEqual((documents[0]["format"], ciencia["estado"]), ("pdf", "desconhecida"))


if __name__ == "__main__":
    unittest.main()


MENU = f"""<html><script>add_menu("A&ccedil;&otilde;es 1º Grau");
add_menu("Processos Ativos", "/processosAdvogado.do?_tj={TJ}01", "userMainFrame", "");
add_menu("A&ccedil;&otilde;es 2º Grau");
add_menu("Recursos Ativos", "/processo/recursal/recursoBusca.do?_tj={TJ}02", "userMainFrame", "");
add_menu("Intima&ccedil;&otilde;es");
add_menu("Novas (Aguardando Leitura)", "/processo/intimacaoAdvogado.do?_tj={TJ}03", "userMainFrame", "");
add_menu("Já Cumpridas", "/processo/intimacaoAdvogado.do?_tj={TJ}04", "userMainFrame", "");
add_menu("Cita&ccedil;&otilde;es");
add_menu("J&aacute; Cumpridas", "/citacao/citacaoAdvogado.do?_tj={TJ}05", "userMainFrame", "");
add_menu("Outros");
add_menu("Caixa de Mensagens", "/util/caixaMensagens.do?_tj={TJ}06", "userMainFrame", "");</script></html>"""
OTHER = "0000002-34.2026.8.04.0001"


def nav(total, first, last):
    return (
        f'<div id="navigator"><div class="navLeft">{total} registro(s) encontrado(s), exibindo de {first} até {last}'
        "</div></div>"
    )


def intimation_page(rows, total, first, last, form="intimacaoAdvogadoForm", action="processo/intimacaoAdvogado.do"):
    body = (
        "".join(
            f"""<tr class="even"><td><a href="/projudi/processo.do?_tj={TJ}a{n}" class="link"><em>{number}</em></a></td>
<td> Parte Exemplo {n} ( Executado ) </td><td>27/07/2026</td><td>30/07/2026</td><td>15 dias úteis</td>
<td><a href="/projudi/processo/intimacao.do?_tj={TJ}b{n}" class="link">Visualizar</a></td></tr>"""
            for n, number in rows
        )
        or '<tr><td colspan="6">Nenhum registro encontrado</td></tr>'
    )
    return f"""<html><a href="/projudi/usuario/logon.do?actionType=logout">Sair</a>
<form name="{form}" id="{form}" method="post" action="/projudi/{action}?actionType=listar">
<input type="text" name="processo" value=""><select name="tipo"><option value="novas" selected>Novas</option>
<option value="lidaECumprida">Já Cumpridas</option></select>
<input type="radio" name="instancia" value="primeiroGrau" checked><input type="radio" name="instancia" value="segundoGrau">
<input type="button" name="pesquisar" value="Pesquisar" onclick="filtrar();">
<input type="hidden" name="{form[:-4]}PageSize" value="2"><input type="hidden" name="{form[:-4]}PageNumber" value="1">
{nav(total, first, last)}
<table class="resultTable"><thead><tr><th> Número do Processo/Recurso </th><th> Parte Intimada </th>
<th><a href="javascript:void(0)">Dt. Postagem</a></th><th> Data da Intimação </th><th> Prazo </th><th></th></tr></thead>
<tbody>{body}</tbody></table></form>
<form name="anotacaoAssessoriaForm" action="/projudi/processo/anotacaoAssessoria.do?actionType=listar"></form></html>"""


PROCESS_LIST = f"""<html><form name="processosAdvogadoForm" method="post" action="/projudi/processosAdvogado.do?actionType=listar">
<select name="situacao"><option value="todos">Todos</option><option value="ativo" selected>Ativo</option></select>
<input type="hidden" name="processosAdvogadoPageSize" value="20"><input type="hidden" name="processosAdvogadoPageNumber" value="1">
<input type="hidden" name="tipo" value="1010">{nav(1, 1, 1)}
<table class="resultTable"><thead><tr><th><input type="checkbox" name="checker" value="checker"></th><th>Processo</th>
<th>Partes</th><th>Dt Distribuição<br/>Dt. Arquiv.</th><th>Classe Processual<br>(Assunto Principal)</th></tr></thead>
<tbody><tr id="linha1"><td><input type="checkbox" name="processos" value="1"></td>
<td><a href="/projudi/processo.do?_tj={TJ}c1" class="link"><em>{NUMBER}</em></a></td>
<td><table class="form"><tr><td><font>Exequente:</font></td><td><ul><li>ENTE EXEMPLO</li></ul></td></tr>
<tr><td colspan="2"></td></tr><tr><td><font>Executado:</font></td><td><ul><li>Empresa Exemplo Ltda</li>
<li>Pessoa Exemplo</li></ul></td></tr></table></td><td>27/08/2020</td>
<td>Execução Fiscal (Dívida Ativa)</td></tr></tbody></table>
<input type="button" name="substabelecerButton" value="Substabelecer" onclick="substabelecer();"></form></html>"""
APPEAL_LIST = f"""<html><form name="recursoBuscaForm" method="post"
 action="/projudi/processo/recursal/recursoBusca.do?actionType=listarParaAdvogadoPoloSituacao">
<input type="hidden" name="ativosPageSize" value="100"><input type="hidden" name="ativosPageNumber" value="1">
{nav(0, 1, 0)}<table class="resultTable"><thead><tr><th></th><th>Recurso</th><th>Partes</th><th>Classe</th>
<th>Dt Distribuição Dt. Arquiv.</th><th>Vara de Origem</th></tr></thead>
<tbody><tr><td colspan="6">Nenhum registro encontrado</td></tr></tbody></table></form></html>"""
DESK_HOME = """<html><div id="tabprefix0">1º Grau Ativos: 1 Suspensos: 0 Arquivados provisoriamente: 0
Arquivados sem distribuição: 0 Arquivados em definitivo: 2 2º Grau Ativos: 0 Suspensos: 0</div></html>"""


class ListsTest(unittest.TestCase):
    """The desk's lists (observed live on 2026-11-23): menu link for page 1, the list's own form for the rest."""

    def setUp(self):
        wired(self)

    def routes(self, *, pages=None, posts=None):
        pages = {
            APP + "usuario/postLogon.do?actionType=iniciar": MENU,
            APP + f"processo/intimacaoAdvogado.do?_tj={TJ}03": intimation_page([(1, NUMBER), (2, OTHER)], 3, 1, 2),
            APP + f"processo/intimacaoAdvogado.do?_tj={TJ}04": intimation_page([], 0, 1, 0),
            APP + f"citacao/citacaoAdvogado.do?_tj={TJ}05": intimation_page(
                [], 0, 1, 0, "citacaoAdvogadoForm", "citacao/citacaoAdvogado.do"
            ),
            APP + f"processosAdvogado.do?_tj={TJ}01": PROCESS_LIST,
            APP + f"processo/recursal/recursoBusca.do?_tj={TJ}02": APPEAL_LIST,
            DESK: DESK_HOME,
            **(pages or {}),
        }

        def routes(method, url, kwargs):
            if method == "GET" and url in pages:
                return response(200, pages[url])
            if method == "POST" and url == DESK:
                return response(200, AGENDA_PENDING)
            if method == "POST" and url == APP + "processo/intimacaoAdvogado.do?actionType=listar":
                data = dict(kwargs["data"])
                if data["instancia"] == "segundoGrau":
                    return response(200, intimation_page([], 0, 1, 0))
                # Past the end the portal answers the last rows again ("exibindo de 21 até 1").
                return response(200, intimation_page([(3, NUMBER)], 3, 3, 3))
            raise AssertionError(f"unexpected {method} {url}")

        return routes

    def test_menu_sections_tell_apart_lists_with_the_same_label(self):
        entries = tjam.menu_entries(MENU)
        self.assertIn(("Intimações", "Já Cumpridas", f"/processo/intimacaoAdvogado.do?_tj={TJ}04"), entries)
        self.assertIn(("Citações", "Já Cumpridas", f"/citacao/citacaoAdvogado.do?_tj={TJ}05"), entries)

    def test_intimations_read_every_page_and_never_open_one(self):
        session = FakeSession(self.routes())
        result = tjam.ADAPTER.list_intimations(session, KEY, lists=["novas", "cumpridas", "citacoesCumpridas"])
        novas, cumpridas, citacoes = result["lists"]
        self.assertEqual((novas["total"], novas["returned"], novas["complete"]), (3, 3, True))
        self.assertEqual(
            novas["rows"][0],
            {
                "processNumber": NUMBER,
                "parte": "Parte Exemplo 1 ( Executado )",
                "postagem": "27/07/2026",
                "dataIntimacao": "30/07/2026",
                "prazo": "15 dias úteis",
                "cienciaPendente": True,
            },
        )
        self.assertEqual((cumpridas["total"], cumpridas["rows"], citacoes["section"]), (0, [], "Citações"))
        self.assertEqual(result["awaitingCiencia"], {"processNumbers": [NUMBER], "complete": True})
        self.assertTrue(result["complete"])
        page2 = dict(session.posts()[0][2]["data"])
        self.assertEqual((page2["intimacaoAdvogadoPageNumber"], page2["tipo"]), ("2", "novas"))
        self.assertNotIn("pesquisar", page2)
        self.assertFalse(any("intimacao.do" in call[1] or "caixaMensagens" in call[1] for call in session.calls))
        self.assertNotIn(TJ, str(result))

    def test_the_second_instance_posts_the_list_form_with_its_radio(self):
        session = FakeSession(self.routes())
        result = tjam.ADAPTER.list_intimations(session, KEY, lists=["novas"], instances=("2",))
        self.assertEqual(dict(session.posts()[0][2]["data"])["instancia"], "segundoGrau")
        self.assertEqual((result["lists"][0]["instance"], result["lists"][0]["total"]), ("2", 0))

    def test_max_pages_bounds_the_read_and_reports_it_incomplete(self):
        session = FakeSession(self.routes())
        result = tjam.ADAPTER.list_intimations(session, KEY, lists=["novas"], max_pages=1)
        self.assertEqual((result["lists"][0]["returned"], result["complete"]), (2, False))

    def test_processes_with_parties_by_role_and_the_desk_counters(self):
        session = FakeSession(self.routes())
        result = tjam.ADAPTER.list_processes(session, KEY)
        first, appeals = result["groups"]
        self.assertEqual(
            first["processes"],
            [
                {
                    "processNumber": NUMBER,
                    "partes": [
                        {"role": "Exequente", "names": ["ENTE EXEMPLO"]},
                        {"role": "Executado", "names": ["Empresa Exemplo Ltda", "Pessoa Exemplo"]},
                    ],
                    "distribuicao": "27/08/2020",
                    "arquivamento": "",
                    "classe": "Execução Fiscal (Dívida Ativa)",
                }
            ],
        )
        self.assertEqual(
            (first["instance"], first["complete"], appeals["instance"], appeals["total"]), ("1", True, "2", 0)
        )
        self.assertEqual(result["deskCounts"]["1"]["arquivados em definitivo"], 2)
        self.assertEqual(result["deskCounts"]["2"], {"ativos": 0, "suspensos": 0})
        self.assertFalse(session.posts())

    def test_unknown_lists_and_a_form_posting_elsewhere_are_errors(self):
        session = FakeSession(self.routes())
        with self.assertRaises(ValueError):
            tjam.ADAPTER.list_intimations(session, KEY, lists=["lidas"])
        with self.assertRaises(ValueError):
            tjam.ADAPTER.list_processes(session, KEY, situation="arquivados")
        elsewhere = intimation_page([(1, NUMBER)], 1, 1, 1, action="processo/cumprirIntimacaoEmLote.do")
        with self.assertRaises(base.ProjudiParseError):
            tjam.list_form(elsewhere, portals.projudi(KEY))
        with self.assertRaises(base.ProjudiParseError):
            tjam.parse_list_page("<html>Erro</html>")
