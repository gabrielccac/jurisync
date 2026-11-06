"""Agent views of DJEN results (lines, summary, checks) and the agent CLI, without network requests."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from unittest import mock

from jurisync.connectors.djen import cli, render
from jurisync.connectors.djen import server as djen_server


def raw(n, tipo="Intimação", texto="", processo="0011664-61.2026.4.05.8303"):
    return {
        "id": n,
        "hash": f"H{n}",
        "numeroprocessocommascara": processo,
        "numero_processo": processo.replace("-", "").replace(".", ""),
        "siglaTribunal": "TRF5",
        "nomeOrgao": "38ª Vara Federal PE",
        "tipoDocumento": tipo,
        "data_disponibilizacao": "2026-10-28",
        "texto": texto,
        "link": "https://pje1g.trf5.jus.br/x",
        "destinatarios": [{"nome": f"PARTE {n}", "polo": "A"}],
        "destinatarioadvogados": [{"advogado": {"nome": "ADV", "numero_oab": "14663", "uf_oab": "PB"}}],
    }


HEADER = "PODER JUDICIÁRIO JUSTIÇA FEDERAL DA 5ª REGIÃO PROCESSO: 0011664 POLO PASSIVO: INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS "
PAGE = [
    raw(
        1,
        texto=HEADER + "De ordem do(a) MM Juiz(a), ficam as partes intimadas para se manifestarem sobre o(s) laudo(s).",
    ),
    raw(
        2,
        texto=HEADER + "De ordem do(a) MM Juiz(a), ficam as partes intimadas para se manifestarem sobre o(s) laudo(s).",
    ),
    raw(3, texto="ATO ORDINATÓRIO (Intimação da perícia) Data da perícia: 27/11/2026 Horário da perícia: 14:10 laudo"),
    raw(4, "Sentença", "Relatório longo. Ante o exposto, julgo PROCEDENTE o pedido."),
    raw(5, "Sentença", "Documento protegido por sigilo - Para visualizá-lo, acesse os autos digitais."),
    raw(6, texto="Ficam as partes intimadas do cancelamento da perícia."),
    raw(7, texto="Texto sem nenhum marcador conhecido."),
]


def compact(items):
    from jurisync.connectors.djen.documents import resolve_portal
    from jurisync.connectors.djen.normalize import compact_item, normalize_item

    out = []
    for r in items:
        item = normalize_item(r)
        item["portal"] = resolve_portal(item)
        out.append(compact_item(item, 0))
    return out


class RenderChecks(unittest.TestCase):
    def setUp(self):
        self.items = compact(PAGE)

    def test_categories_follow_the_text(self):
        cats = [render.categoria(i) for i in self.items]
        self.assertEqual(cats, ["laudo", "laudo", "pericia", "sentenca", "sigilo", "pericia_cancelada", "outro"])

    def test_nucleo_skips_header_and_filler_and_finds_the_ruling(self):
        self.assertTrue(render.nucleo(self.items[0]).startswith("Ficam as partes intimadas para se manifestarem"))
        self.assertTrue(render.nucleo(self.items[2]).startswith("perícia 27/11/2026 14:10 · "))
        self.assertTrue(render.nucleo(self.items[3]).startswith("Ante o exposto, julgo PROCEDENTE"))

    def test_nucleo_puts_the_deadline_first(self):
        item = compact(
            [raw(9, texto="Fica a parte autora intimada para, no prazo de 10 (dez) dias, emendar a inicial.")]
        )[0]
        self.assertTrue(render.nucleo(item).startswith("prazo citado 10 dias · "))

    def test_cancelled_items_are_flagged(self):
        item = compact([raw(9, texto="Ficam intimadas.")])[0]
        item["ativo"], item["data_cancelamento"] = False, "2026-10-29"
        self.assertIn("[CANCELADA 2026-10-29] 0011664", render.lines([item]))

    def test_lines_print_shared_values_once(self):
        text = render.lines(self.items)
        self.assertIn("# data=2026-10-28 tribunal=TRF5", text)
        self.assertEqual(text.count("2026-10-28"), 1)

    def test_summary_groups_and_keeps_sealed_and_unknown_items(self):
        text = render.summary(self.items)
        self.assertIn("## laudo (2)", text)
        self.assertEqual(text.count("Ficam as partes intimadas para se manifestarem"), 1)  # shared núcleo once
        for name in ("## sigilo (1)", "## outro (1)", "## pericia_cancelada (1)"):
            self.assertIn(name, text)
        self.assertIn("H5", text)

    def test_so_acao_drops_only_cancelled_pericia(self):
        kept = render.filter_items(self.items, so_acao=True)
        self.assertEqual(len(kept), len(self.items) - 1)

    def test_checks_flag_missing_and_repeated_items(self):
        items, removed = render.dedupe(self.items + self.items[:1])
        self.assertEqual(removed, 1)
        ok = render.checks(8, items, removed, 1, True)
        self.assertEqual((ok["status"], ok["obtido"], ok["unicos"]), ("ok", 8, 7))
        short = render.checks(20, items, 0, 1, True)
        self.assertEqual(short["status"], "parcial")
        more = render.checks(20, items, 0, 1, False)
        self.assertIn("faltam 13 itens em outras páginas", more["avisos"])


class Cli(unittest.TestCase):
    def run_cli(self, argv, pages):
        calls = []

        def search_page(params, pagina, itens):
            calls.append((dict(params), pagina, itens))
            return pages[pagina - 1]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "search_page", search_page), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue(), calls

    def test_tudo_reads_every_page_and_reports_ok(self):
        pages = [{"count": 7, "items": PAGE[:5]}, {"count": 7, "items": PAGE[5:]}]
        argv = ["buscar", "--dia", "2026-10-28", "--oab", "14663", "--uf", "PB", "--itens", "5", "--tudo"]
        code, out, _, calls = self.run_cli(argv, pages)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual([c[1] for c in calls], [1, 2])
        self.assertTrue(out.startswith("status=ok declarado=7 obtido=7"))
        self.assertEqual(calls[0][0]["numeroOab"], "14663")

    def test_one_page_of_many_is_partial(self):
        code, out, _, _ = self.run_cli(
            ["buscar", "--dia", "2026-10-28", "--oab", "14663", "--uf", "PB", "--itens", "5"],
            [{"count": 7, "items": PAGE[:5]}],
        )
        self.assertEqual(code, cli.EXIT_PARTIAL)
        self.assertIn("aviso: faltam 2 itens", out)

    def test_missing_search_key_is_a_usage_error(self):
        code, out, err, calls = self.run_cli(["buscar", "--dia", "2026-10-28", "--tribunal", "TRF5"], [])
        self.assertEqual((code, out, calls), (cli.EXIT_USAGE, "", []))
        self.assertTrue(err.startswith("erro codigo=uso"))

    def test_saida_writes_full_jsonl_and_prints_only_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dia.jsonl")
            code, out, _, _ = self.run_cli(
                ["buscar", "--dia", "2026-10-28", "--oab", "14663", "--uf", "PB", "--saida", path],
                [{"count": 7, "items": PAGE}],
            )
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh]
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(rows), 7)
        self.assertIn("Ante o exposto", rows[3]["texto"])
        self.assertNotIn("H1 |", out)
        self.assertIn("linhas=7", out)

    def test_json_cuts_text_and_keeps_routing_fields(self):
        _, out, _, _ = self.run_cli(
            ["buscar", "--dia", "2026-10-28", "--oab", "14663", "--uf", "PB", "--json", "--texto-max", "20"],
            [{"count": 7, "items": PAGE}],
        )
        data = json.loads(out)
        self.assertEqual(data["checagem"]["status"], "ok")
        self.assertEqual(data["items"][0]["advogados"][0]["oab"], "14663/PB")
        self.assertTrue(data["items"][0]["texto_truncado"])


class CliAgentContract(unittest.TestCase):
    ARGS = ["buscar", "--dia", "2026-10-28", "--oab", "14663", "--uf", "PB"]

    def run_cli(self, argv, pages=None, raises=None):
        def search_page(params, pagina, itens):
            if raises:
                raise raises
            return pages[pagina - 1]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "search_page", search_page), redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_every_help_ends_with_copyable_examples(self):
        for command in ("buscar", "salvas", "certidao", "prazo"):
            code, out, _ = self.run_cli([command, "--help"])
            self.assertEqual(code, 0)
            self.assertIn("Exemplos:", out)
            self.assertIn(f"djen-cli {command}", out)

    def test_usage_errors_carry_a_working_example(self):
        code, _, err = self.run_cli(["buscar", "--dia", "2026-10-28", "--tribunal", "TRF5"])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("exemplo=djen-cli buscar --dia", err)
        code, _, err = self.run_cli(["prazo", "--dias", "5"])  # argparse's own error uses the same contract
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertTrue(err.startswith("erro codigo=uso"))
        self.assertIn("exemplo=djen-cli prazo", err)

    def test_unknown_category_fails_instead_of_returning_nothing(self):
        code, out, err = self.run_cli([*self.ARGS, "--categoria", "laudos"], [{"count": 7, "items": PAGE}])
        self.assertEqual((code, out), (cli.EXIT_USAGE, ""))
        self.assertIn("valores aceitos: ", err)

    def test_network_failure_uses_the_unavailable_exit_code(self):
        code, _, err = self.run_cli(self.ARGS, raises=urllib.error.URLError("sem rede"))
        self.assertEqual(code, cli.EXIT_UNAVAILABLE)
        self.assertIn("proximo=tente de novo", err)

    def test_failures_the_client_wraps_are_split_into_retry_and_real_errors(self):
        for message, expected in (
            ("DJEN request failed after 3 attempt(s): URLError", cli.EXIT_UNAVAILABLE),
            ("DJEN request failed after 3 attempt(s): HTTP 503", cli.EXIT_UNAVAILABLE),
            ("DJEN request failed after 1 attempt(s): HTTP 400", cli.EXIT_SOURCE),
            ("DJEN response has invalid items", cli.EXIT_SOURCE),
        ):
            code, _, _ = self.run_cli(self.ARGS, raises=cli.DjenError(message))
            self.assertEqual(code, expected, message)

    def test_screen_output_is_bounded_and_says_how_to_get_the_rest(self):
        many = [raw(n, texto="Ficam as partes intimadas.") for n in range(1, 131)]
        code, out, _ = self.run_cli(
            [*self.ARGS, "--itens", "50", "--tudo", "--texto-max", "0"],
            [
                {"count": 130, "items": many[:50]},
                {"count": 130, "items": many[50:100]},
                {"count": 130, "items": many[100:]},
            ],
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(out.count(" | H"), 100)
        self.assertIn("# próximo: mostrando 100 de 130", out)
        _, unbounded, _ = self.run_cli(
            [*self.ARGS, "--itens", "50", "--tudo", "--limite", "0", "--texto-max", "0"],
            [
                {"count": 130, "items": many[:50]},
                {"count": 130, "items": many[50:100]},
                {"count": 130, "items": many[100:]},
            ],
        )
        self.assertEqual(unbounded.count(" | H"), 130)

    def test_partial_page_tells_what_to_run(self):
        _, out, _ = self.run_cli([*self.ARGS, "--itens", "5"], [{"count": 7, "items": PAGE[:5]}])
        self.assertIn("# próximo: faltam 2 itens: repita com --tudo", out)

    def test_campos_projects_json_and_rejects_unknown_fields(self):
        _, out, _ = self.run_cli(
            [*self.ARGS, "--json", "--campos", "hash,tipo_documento"], [{"count": 7, "items": PAGE}]
        )
        self.assertEqual(set(json.loads(out)["items"][0]), {"hash", "tipo_documento"})
        code, _, err = self.run_cli([*self.ARGS, "--json", "--campos", "hash,cor"], [{"count": 7, "items": PAGE}])
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("campos aceitos:", err)

    def test_saida_dash_streams_ndjson_and_keeps_checks_on_stderr(self):
        code, out, err = self.run_cli([*self.ARGS, "--saida", "-", "--campos", "hash"], [{"count": 7, "items": PAGE}])
        self.assertEqual(code, cli.EXIT_OK)
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([r["hash"] for r in rows], [f"H{n}" for n in range(1, 8)])
        self.assertTrue(err.startswith("status=ok declarado=7"))

    def test_ler_starts_at_the_ruling_not_at_the_court_header(self):
        long_ruling = raw(
            9,
            "Sentença",
            "PODER JUDICIÁRIO 38ª Vara Federal PE " + "relatório " * 600 + "Ante o exposto, julgo PROCEDENTE.",
        )
        _, out, _ = self.run_cli(
            [*self.ARGS, "--ler", "sentenca", "--ler-max", "200"], [{"count": 1, "items": [long_ruling]}]
        )
        self.assertIn("Ante o exposto, julgo PROCEDENTE.", out)
        self.assertNotIn("relatório relatório", out)

    def test_ler_prints_the_asked_categories_only(self):
        _, out, _ = self.run_cli([*self.ARGS, "--resumo", "--ler", "sentenca"], [{"count": 7, "items": PAGE}])
        self.assertIn("### H4 0011664-61.2026.4.05.8303 (sentenca)", out)
        self.assertIn("Ante o exposto, julgo PROCEDENTE o pedido.", out)
        self.assertNotIn("### H1 ", out)
        self.assertIn("# ler a ordem/dispositivo antes de concluir: ", out)

    def test_summary_counts_cover_the_whole_filtered_set_not_only_the_screen(self):
        _, out, _ = self.run_cli([*self.ARGS, "--resumo", "--limite", "2"], [{"count": 7, "items": PAGE}])
        self.assertIn("laudo=2", out)
        self.assertIn("pedem leitura: 6 de 7", out)
        self.assertIn("# próximo: mostrando 2 de 7", out)


class McpFormats(unittest.TestCase):
    def search(self, **extra):
        page = {"count": 7, "items": PAGE}
        with mock.patch.object(djen_server, "search_page", return_value=page):
            return djen_server.search_communications(
                inicio=date(2026, 10, 28), fim=date(2026, 10, 28), numeroOab="14663", ufOab="PB", **extra
            )

    def test_default_stays_json_items(self):
        result = self.search(itens=10)
        self.assertEqual(len(result["items"]), 7)
        self.assertIn("advogados", result["items"][0])

    def test_text_views_return_checks_and_text(self):
        for formato in ("linhas", "resumo"):
            result = self.search(itens=10, formato=formato)
            self.assertEqual(set(result), {"checagem", "texto"})
            self.assertEqual(result["checagem"]["status"], "ok")
        self.assertIn("## laudo (2)", self.search(itens=10, formato="resumo")["texto"])


if __name__ == "__main__":
    unittest.main()
