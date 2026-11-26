import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from docx import Document

from jurisync.workflows.petitions import __main__ as cli
from jurisync.workflows.petitions import auditoria, calc, minuta, render
from jurisync.workflows.petitions.case import AuthorCase


def complete_dossier():
    return {
        "peca": {"cenario": "atraso"},
        "processo": {"comarca": "Cidade Ficticia/GO", "valor_moral": "20.000,00"},
        "doc": {"reserva": "05. Passagem.pdf"},
        "autores": [
            {
                "autor": {
                    "nome": "Pessoa Um",
                    "cpf": "000.000.000-00",
                    "menor": False,
                    "genero": "feminino",
                    "numero": "singular",
                    "despesas": [{"valor": "100,50"}],
                }
            },
            {
                "autor": {
                    "nome": "Pessoa Dois",
                    "cpf": "111.111.111-11",
                    "menor": True,
                    "genero": "masculino",
                    "numero": "singular",
                }
            },
        ],
    }


class CalcTest(unittest.TestCase):
    def test_money_and_words(self):
        self.assertEqual(calc.money("20.000,50"), Decimal("20000.50"))
        self.assertEqual(calc.format_money(Decimal("21675.46")), "21.675,46")
        self.assertEqual(calc.money_in_words(Decimal("20000")), "vinte mil reais")
        self.assertEqual(
            calc.money_in_words(Decimal("21675.46")),
            "vinte e um mil seiscentos e setenta e cinco reais e quarenta e seis centavos",
        )
        self.assertEqual(calc.money_in_words(Decimal("1000000")), "um milhão de reais")
        self.assertEqual(calc.date_in_words("2026-10-09"), "09 de outubro de 2026")


class CaseCalcTest(unittest.TestCase):
    def test_des_uses_the_highest_quotation_and_ceiling_is_flagged(self):
        dossier = complete_dossier()
        dossier["peca"]["usar_tese_pretericao"] = True
        dossier["viagem"] = {"internacional": False}
        dossier["processo"]["des_cotacao_data_voo"] = "7,1000"
        dossier["processo"]["des_cotacao_data_peca"] = "7,4500"
        dossier["escritorio"] = {"teto_juizado": "20.000,00"}
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["des_valor_aprox"], "1.862,50")
        self.assertTrue(case.calculated["acima_do_teto"])

    def test_double_refund_doubles_only_what_the_defendant_charged(self):
        dossier = complete_dossier()
        dossier["peca"]["repeticao_em_dobro"] = True
        dossier["autores"][0]["autor"]["despesas"] = [
            {"valor": "100,50", "pago_a": "re"},
            {"valor": "617,16", "pago_a": "terceiro"},
        ]
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["valor_restituivel"], "100,50")
        self.assertEqual(case.calculated["valor_material_dobro"], "201,00")
        self.assertEqual(case.calculated["valor_material_terceiros"], "617,16")
        self.assertEqual(case.calculated["valor_causa"], "20.818,16")
        self.assertFalse(case.calculated["despesas_sem_pagador"])

    def test_expense_paid_to_a_third_party_is_never_doubled(self):
        dossier = complete_dossier()
        dossier["peca"]["repeticao_em_dobro"] = True
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertNotIn("valor_material_dobro", case.calculated)
        self.assertEqual(case.calculated["valor_causa"], "20.100,50")
        self.assertTrue(case.calculated["despesas_sem_pagador"])

    def test_delays_are_calculated_from_dates_and_times(self):
        dossier = complete_dossier()
        dossier.setdefault("viagem", {}).update(
            {
                "data_voo_original": "2026-08-03",
                "horario_partida_original": "18:30",
                "novo_horario_partida": "05:15",
                "horario_decolagem_efetivo": "05:19",
                "horario_chegada_original": "23:40",
                "data_chegada_efetiva": "2026-08-04",
                "horario_chegada_efetivo": "10:20",
                "data_comunicacao": "2026-08-03",
                "hora_comunicacao": "16:00",
            }
        )
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["atraso_anunciado"], "10 horas e 45 minutos")
        self.assertEqual(case.calculated["atraso_efetivo"], "10 horas e 49 minutos")
        self.assertEqual(case.calculated["atraso_efetivo_piso"], "10 horas")
        self.assertEqual(case.calculated["atraso_chegada"], "10 horas e 40 minutos")
        self.assertEqual(case.calculated["antecedencia_comunicacao"], "2 horas e 30 minutos")
        self.assertTrue(case.calculated["atraso_4h_ou_mais"])
        self.assertEqual(calc.duration_text(60), "1 hora")
        self.assertEqual(calc.duration_text(45), "45 minutos")

    def test_communication_after_departure_has_no_advance_notice(self):
        dossier = complete_dossier()
        dossier.setdefault("viagem", {}).update(
            {
                "data_voo_original": "2026-08-03",
                "horario_partida_original": "18:30",
                "data_comunicacao": "2026-08-04",
                "hora_comunicacao": "05:15",
            }
        )
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertTrue(case.calculated["comunicacao_apos_partida"])
        self.assertNotIn("antecedencia_comunicacao", case.calculated)

    def test_passenger_counts_are_written_by_the_system(self):
        dossier = complete_dossier()
        dossier["viagem"] = {"numero_passageiros": 3}
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["n_passageiros_extenso"], "três")
        self.assertEqual(case.calculated["n_acompanhantes"], "2")
        self.assertEqual(case.calculated["n_acompanhantes_extenso"], "dois")

    def test_panel_photo_before_departure_is_not_a_delay(self):
        dossier = complete_dossier()
        dossier["viagem"] = {
            "data_voo_original": "2026-07-23",
            "horario_partida_original": "18:05",
            "horario_painel": "17:48",
        }
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertTrue(case.calculated["painel_antes_da_partida"])
        self.assertNotIn("tempo_painel_apos_partida", case.calculated)

    def test_des_quantity_does_not_wait_for_the_quotation(self):
        dossier = complete_dossier()
        dossier["peca"]["usar_tese_pretericao"] = True
        dossier.setdefault("viagem", {})["internacional"] = False
        for name in ("des_cotacao", "des_cotacao_data_voo", "des_cotacao_data_peca"):
            dossier["processo"].pop(name, None)
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["des_quantidade"], "250")
        self.assertNotIn("des_valor_aprox", case.calculated)


class MoneyTest(unittest.TestCase):
    def test_brazilian_and_plain_amounts(self):
        cases = {
            20000: "20000",
            20000.5: "20000.50",
            "20000": "20000",
            "20000.50": "20000.50",
            "20000.5": "20000.5",
            "20.000": "20000",
            "20.000,50": "20000.50",
            "1.234.567,89": "1234567.89",
            "1.234.567": "1234567",
            "1.234": "1234",
            "100,5": "100.5",
            "R$ 5.000,00": "5000.00",
            "R$5.000": "5000",
            "5000,00": "5000.00",
            "0,50": "0.50",
            "0.50": "0.50",
            " 7 ": "7",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(calc.money(value), Decimal(expected))
        self.assertEqual(calc.money(Decimal("12.345")), Decimal("12.345"))
        self.assertIsNone(calc.money(None))
        self.assertIsNone(calc.money(""))

    def test_ambiguous_or_malformed_amounts_are_refused(self):
        for value in (
            "1.23.4",
            "20.0000",
            "01.234",
            "1.234.5",
            "20.000.50",
            "20,000.50",
            "1,2,3",
            "5.000,000",
            "12.34,56",
            "0.000",
            "abc",
            "-5,00",
            "1e3",
            "R$",
            True,
            -5,
            float("nan"),
            ["1"],
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid amount"):
                calc.money(value)

    def test_the_claim_value_is_not_a_thousand_times_smaller(self):
        dossier = complete_dossier()
        dossier["processo"]["valor_moral"] = "20.000"
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["valor_causa"], "20.100,50")


class WordsTest(unittest.TestCase):
    def test_grammar_of_large_amounts(self):
        cases = {
            "1001": "mil e um reais",
            "1100": "mil e cem reais",
            "1234": "mil duzentos e trinta e quatro reais",
            "100000": "cem mil reais",
            "1001000": "um milhão e mil reais",
            "1200000": "um milhão e duzentos mil reais",
            "1234000": "um milhão duzentos e trinta e quatro mil reais",
            "2000100": "dois milhões e cem reais",
            "2000000": "dois milhões de reais",
            "1000000000": "um bilhão de reais",
            "2000001000": "dois bilhões e mil reais",
            "1000000001": "um bilhão e um reais",
            "0.01": "um centavo",
            "1": "um real",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(calc.money_in_words(Decimal(value)), expected)
        self.assertEqual(
            calc.money_in_words(Decimal("999999999999.99")),
            "novecentos e noventa e nove bilhões novecentos e noventa e nove milhões novecentos e noventa e nove mil "
            "novecentos e noventa e nove reais e noventa e nove centavos",
        )

    def test_amounts_out_of_range_are_refused(self):
        for value in ("-1", "1000000000000000"):
            with self.assertRaisesRegex(ValueError, "amount"):
                calc.money_in_words(Decimal(value))


DRAFT = """@enderecamento AO JUÍZO DO JUIZADO ESPECIAL CÍVEL DA COMARCA DE CIDADE FICTICIA/GO
**PESSOA UM**, brasileira, inscrita no CPF sob o nº 000.000.000-00, vem propor a presente
@titulo AÇÃO DE INDENIZAÇÃO POR DANOS MORAIS DECORRENTES DE ATRASO DE VOO
# I – DOS FATOS
## 1. Do atraso
A Autora pagou R$ 100,50 de alimentação. A decolagem ocorreu às [PENDENTE: horário da decolagem efetiva].
Nesse sentido, o REsp 1.000.000/GO:
> EMENTA: TRANSPORTE AÉREO. Indenização no valor de R$ 6.000,00 mantida. Julgado em 01/02/2020.
%% nota para a revisão: conferir o horário
| Despesa | Valor |
|---|---|
| Alimentação | R$ 100,50 |
# II – DOS PEDIDOS
- a citação da Ré;
- a condenação da Ré ao pagamento de R$ 20.000,00 a título de danos morais;
# III – DO VALOR DA CAUSA
Atribui-se à causa o valor de R$ 20.100,50 (vinte mil e cem reais e cinquenta centavos).
@fecho Nestes termos, pede deferimento.
@assinatura Advogada Fictícia
"""
REFERENCES = "Conforme REsp 1.000.000/GO, o transporte aéreo..."


def audit(draft=DRAFT, dossier=None, group=(0,), references=REFERENCES, root=None):
    dossier = dossier or {**complete_dossier(), "seguir_com_pendencias": "Colaboradora"}
    return auditoria.audit(draft, dossier, list(group), references, root)


class RefundTest(unittest.TestCase):
    def test_partial_refund_is_deducted_and_the_95_percent_is_calculated(self):
        dossier = complete_dossier()
        dossier["peca"]["repeticao_em_dobro"] = True
        dossier["autores"][0]["autor"]["despesas"] = [
            {"valor": "967,02", "taxas": "56,88", "pago_a": "re", "reembolsado": "56,88"}
        ]
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["valor_restituivel"], "910,14")
        self.assertEqual(case.calculated["valor_material_dobro"], "1.820,28")
        self.assertEqual(case.calculated["retencao_maxima_5"], "45,51")  # 5% of the fare (967,02 - 56,88)
        self.assertEqual(case.calculated["restituicao_95"], "864,63")  # 967,02 - 45,51 - 56,88 already returned
        self.assertEqual(case.calculated["valor_causa"], "21.820,28")


class BaggageTest(unittest.TestCase):
    def test_montreal_des_and_foreign_currency_expenses_enter_the_claim(self):
        dossier = complete_dossier()
        dossier["peca"]["usar_des_montreal"] = True
        dossier["processo"]["des_cotacao"] = "7,5000"
        dossier["autores"][0]["autor"]["despesas"] = [
            {"valor_original": "100,00", "moeda": "GBP", "cotacao": "7,1234", "pago_a": "terceiro"}
        ]
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["valor_material_total"], "712,34")
        self.assertEqual(case.calculated["des_montreal_valor"], "11.392,50")
        self.assertEqual(case.calculated["valor_causa"], "32.104,84")


class QuoteTest(unittest.TestCase):
    def test_sdr_is_ptax_dollar_times_imf_dollars_per_sdr_of_a_closed_month(self):
        from jurisync.workflows.petitions import cotacao

        imf = (
            '<Obs TIME_PERIOD="2026-M05" OBS_VALUE="1.35" DERIVATION_TYPE="R"/>'
            '<Obs TIME_PERIOD="2026-M06" OBS_VALUE="1.40" DERIVATION_TYPE="R"/>'
        )

        def fake_get(url, **params):
            response = mock.Mock()
            if "olinda" in url:
                empty = params.get("@dataCotacao") == "'06-21-2026'"
                response.json.return_value = {"value": [] if empty else [{"cotacaoVenda": 5.0}]}
            else:
                response.text = imf
            return response

        with mock.patch.object(cotacao, "_get", side_effect=fake_get):
            result = cotacao.des("2026-06-21")
        self.assertEqual(result["cotacao"], "6,7500")  # 5,00 × 1,35 (May): June had not closed yet
        self.assertIn("20/06/2026", result["fonte"])  # Sunday 21 has no bulletin: the day before
        self.assertIn("2026-05", result["fonte"])

    def test_ceiling_is_forty_minimum_wages_of_the_year(self):
        self.assertEqual(calc.small_claims_ceiling("2026-11-11"), Decimal("64840"))
        dossier = complete_dossier()
        dossier["processo"]["data_peca"] = "2026-11-11"
        case = AuthorCase(dossier, 0)
        calc.apply(case)
        self.assertEqual(case.calculated["teto_juizado"], "64.840,00")
        self.assertFalse(case.calculated["acima_do_teto"])


class DraftFormatTest(unittest.TestCase):
    def test_line_marks_become_parts_of_the_petition(self):
        items = minuta.parse(DRAFT)
        kinds = [item["tipo"] for item in items]
        self.assertEqual(kinds[:5], ["enderecamento", "corpo", "titulo", "secao", "subtitulo"])
        self.assertIn("citacao", kinds)
        self.assertIn("tabela", kinds)
        self.assertEqual(kinds.count("pedido"), 2)
        self.assertNotIn("nota para a revisão", " ".join(minuta.plain(item) for item in items))
        self.assertEqual(minuta.pending_marks(items), ["[PENDENTE: horário da decolagem efetiva]"])
        self.assertEqual(minuta.parse("![Cartão de embarque](provas/cartao.png)")[0]["arquivo"], "provas/cartao.png")
        image = minuta.parse("![Fatura [PENDENTE: data] do cartão](provas/fatura.png)")[0]
        self.assertEqual((image["tipo"], image["arquivo"]), ("imagem", "provas/fatura.png"))

    def test_inline_bold_italic_underline_and_pending(self):
        pieces = minuta.runs("**A** *b* __c__ [PENDENTE: d] e")
        self.assertEqual([piece[0] for piece in pieces], ["A", " ", "b", " ", "c", " ", "[PENDENTE: d]", " e"])
        self.assertTrue(pieces[0][1] and pieces[2][2] and pieces[4][3] and pieces[6][4])


class AuditTest(unittest.TestCase):
    def test_a_sound_draft_passes_and_lists_what_to_review(self):
        report = audit()
        self.assertTrue(report["ok"], report["problemas"])
        self.assertEqual(report["pendencias_no_texto"], ["[PENDENTE: horário da decolagem efetiva]"])
        self.assertIn("REsp 1.000.000", report["citacoes_para_conferir"])

    def test_case_law_outside_the_office_references_is_refused(self):
        report = audit(DRAFT.replace("REsp 1.000.000/GO:", "REsp 2.222.222/SP:"))
        self.assertFalse(report["ok"])
        self.assertTrue(any("2.222.222" in problem for problem in report["problemas"]))
        dossier = {
            **complete_dossier(),
            "seguir_com_pendencias": "Colaboradora",
            "citacoes_novas": [{"referencia": "REsp 2.222.222/SP", "fonte": "advogada"}],
        }
        self.assertTrue(audit(DRAFT.replace("REsp 1.000.000/GO:", "REsp 2.222.222/SP:"), dossier)["ok"])

    def test_a_quote_found_only_in_petitions_read_for_the_case_goes_to_review(self):
        draft = DRAFT.replace("REsp 1.000.000/GO:", "REsp 3.333.333/RJ:")
        dossier = {**complete_dossier(), "seguir_com_pendencias": "Colaboradora"}
        report = auditoria.audit(draft, dossier, [0], REFERENCES, None, "Ementa citada: REsp 3.333.333/RJ")
        self.assertTrue(report["ok"], report["problemas"])
        self.assertIn("REsp 3.333.333", report["citacoes_conferir_na_fonte"])

    def test_open_items_page_cannot_name_other_people(self):
        dossier = {**complete_dossier(), "seguir_com_pendencias": "C", "pendencias_abertas": ["Falar com Pessoa Dois"]}
        self.assertFalse(audit(dossier=dossier)["ok"])

    def test_amounts_dates_claim_value_and_other_people_are_checked(self):
        problems = " ".join(audit(DRAFT.replace("R$ 100,50 de", "R$ 300,00 de"))["problemas"])
        self.assertIn("300,00", problems)
        problems = " ".join(audit(DRAFT.replace("o valor de R$ 20.100,50", "o valor de R$ 20.000,00"))["problemas"])
        self.assertIn("diferente do calculado", problems)
        problems = " ".join(audit(DRAFT + "A viagem foi com Pessoa Dois.\n")["problemas"])
        self.assertIn("não é autora", problems)
        problems = " ".join(audit(DRAFT.replace("R$ 100,50 de", "R$ 100,5000 de"))["problemas"])
        self.assertIn("100,5000", problems)
        problems = " ".join(audit(DRAFT + "O voo era em 03/03/2026.\n")["problemas"])
        self.assertIn("03/03/2026", problems)

    def test_phrases_the_office_forbade_are_refused(self):
        for phrase in (
            "Não há, nos documentos, indicação de causa externa.",
            "O VRA classifica o voo como cancelado, sem justificativa publicada.",
            "Subsidiariamente, a compensação em Direitos Especiais de Saque.",
            "O tempo foi exíguo.",
        ):
            self.assertFalse(audit(DRAFT + phrase + "\n")["ok"], phrase)
        report = audit(DRAFT + "A Ré limitou-se ao traslado entre os aeroportos.\n")
        self.assertTrue(any("assistência" in warning for warning in report["avisos"]))

    def test_open_items_need_someone_who_decided_and_markers_cannot_remain(self):
        problems = " ".join(audit(dossier=complete_dossier())["problemas"])
        self.assertIn("seguir_com_pendencias", problems)
        self.assertFalse(audit(DRAFT + "Ver {{viagem.voo}}\n")["ok"])

    def test_an_image_must_be_inside_the_case_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            report = audit(DRAFT + "![Print](../fora.png)\n", root=folder)
            self.assertFalse(report["ok"])


class WriteTest(unittest.TestCase):
    def template(self, folder):
        document = Document()
        from docx.enum.style import WD_STYLE_TYPE

        for name in ("Corpo", "Secao", "Pedido", "Citacao", "Enderecamento", "Titulo da Acao"):
            document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        document.add_paragraph("texto do modelo que não pode sobrar")
        path = Path(folder) / "timbrado.docx"
        document.save(path)
        return path

    def test_draft_goes_into_the_template_styles_with_lettered_requests_and_open_items_page(self):
        with tempfile.TemporaryDirectory() as folder:
            items = minuta.parse(DRAFT)
            output = render.to_docx(
                items,
                Path(folder) / "minuta.docx",
                template=self.template(folder),
                open_items=["Cotação do DES", {"item": "Profissão da autora", "quem": "cliente"}],
            )
            document = Document(output)
            texts = [paragraph.text for paragraph in document.paragraphs]
            self.assertNotIn("texto do modelo que não pode sobrar", texts)
            self.assertIn("a) a citação da Ré;", texts)
            styles = {paragraph.text[:12]: paragraph.style.name for paragraph in document.paragraphs}
            self.assertEqual(styles["I – DOS FATO"], "Secao")
            self.assertEqual(styles["a) a citação"], "Pedido")
            self.assertTrue(any(text.startswith("PENDÊNCIAS DESTA MINUTA") for text in texts))
            self.assertTrue(
                any("horário da decolagem efetiva" in text and text.startswith("No texto") for text in texts)
            )
            self.assertIn("Cotação do DES.", texts)
            self.assertIn("Resolve: cliente", texts)
            self.assertEqual(len(document.tables), 1)

    @unittest.skipUnless(shutil.which("pdftoppm"), "pdftoppm (poppler-utils) not installed")
    def test_pdf_proof_becomes_trimmed_pictures(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "prova.pdf"
            from PIL import Image, ImageDraw

            page = Image.new("RGB", (300, 400), "white")
            ImageDraw.Draw(page).rectangle((50, 50, 120, 90), fill="black")
            page.save(pdf)
            output = render.to_docx(minuta.parse("![Prova](prova.pdf)"), Path(folder) / "m.docx", image_root=folder)
            self.assertEqual(len(Document(output).inline_shapes), 1)


class CommandLineTest(unittest.TestCase):
    def run_cli(self, *args):
        with mock.patch("sys.stdout") as stdout:
            code = cli.main(list(args))
        return code, "".join(call.args[0] for call in stdout.write.call_args_list)

    def test_calculate_audit_and_write(self):
        with tempfile.TemporaryDirectory() as folder:
            dossier = Path(folder) / "dossie.json"
            dossier.write_text(
                json.dumps({**complete_dossier(), "seguir_com_pendencias": "Colaboradora"}), encoding="utf-8"
            )
            draft = Path(folder) / "minuta.md"
            draft.write_text(DRAFT, encoding="utf-8")
            references = Path(folder) / "refs.txt"
            references.write_text(REFERENCES, encoding="utf-8")
            code, output = self.run_cli("calcular", "--dossie", str(dossier))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["valor_causa"], "20.100,50")
            code, _ = self.run_cli("auditar", "--dossie", str(dossier), "--minuta", str(draft))
            self.assertEqual(code, 3)  # without references the REsp is unknown
            with mock.patch.object(render, "to_pdf", return_value=Path(folder) / "minuta.pdf"):
                code, output = self.run_cli(
                    "gravar",
                    "--dossie",
                    str(dossier),
                    "--minuta",
                    str(draft),
                    "--referencias",
                    str(references),
                    "--saida",
                    str(Path(folder) / "minuta.docx"),
                )
            self.assertEqual(code, 0, output)
            self.assertTrue((Path(folder) / "minuta.docx").is_file())
            code, output = self.run_cli("calcular", "--dossie", str(dossier), "--autor", "9")
            self.assertEqual(code, 2)


class FlightRecordTest(unittest.TestCase):
    def test_flight_is_found_by_ticket_code_and_day_in_the_cached_month(self):
        from jurisync.workflows.petitions import voo

        header = (
            "Sigla ICAO Empresa Aérea;Empresa Aérea;Número Voo;Código DI;Código Tipo Linha;Modelo Equipamento;"
            "Número de Assentos;Sigla ICAO Aeroporto Origem;Descrição Aeroporto Origem;Partida Prevista;Partida Real;"
            "Sigla ICAO Aeroporto Destino;Descrição Aeroporto Destino;Chegada Prevista;Chegada Real;Situação Voo;"
            "Justificativa;Referência;Situação Partida;Situação Chegada;Codeshare\n"
        )
        row = (
            "AZU;AZUL;2618;0;N;A20N;174;SBKP;VIRACOPOS;26/09/2026 22:25;;SBGO;GOIANIA;26/09/2026 23:55;;CANCELADO;;"
            "26/09/2026 00:00:00;;;\n"
        )
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", {"JURISYNC_CACHE_DIR": folder}):
            (Path(folder) / "vra").mkdir()
            (Path(folder) / "vra" / "VRA_2026_09.csv").write_text(header + row + " " * 1000, encoding="utf-8")
            with mock.patch.object(voo, "requests") as http:
                http.get.return_value.status_code = 404
                http.exceptions.RequestException = Exception
                records = voo.find("AD", "2618", "2026-09-26")
                self.assertEqual(records[0]["situacao"], "CANCELADO")
                self.assertIn("ANAC", records[0]["fonte"])
                self.assertEqual(voo.find("AD", "2618", "2026-09-25"), [])
                with self.assertRaises(voo.FlightError):
                    voo.find("AD", "2618", "2026-10-14")


class RegistryTest(unittest.TestCase):
    def test_company_falls_back_and_formats_the_address(self):
        from jurisync.workflows.petitions import cadastro

        fallback = {
            "razao_social": "TAM LINHAS AEREAS S/A.",
            "estabelecimento": {
                "tipo_logradouro": "RUA",
                "logradouro": "ATICA",
                "numero": "673",
                "complemento": "ANDAR 6   SALA 62",
                "bairro": "JARDIM BRASIL",
                "cidade": {"nome": "São Paulo"},
                "estado": {"sigla": "SP"},
                "cep": "04634042",
                "situacao_cadastral": "Ativa",
            },
        }
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.dict("os.environ", {"JURISYNC_CACHE_DIR": folder}),
            mock.patch.object(cadastro, "_get_json", side_effect=[(None, 429), (fallback, 200)]),
        ):
            result = cadastro.company("02.012.862/0001-60")
            self.assertEqual(cadastro.company("02012862000160"), result)  # second call from the cache
        self.assertEqual(result["cnpj"], "02.012.862/0001-60")
        self.assertIn("RUA ATICA, 673, ANDAR 6 SALA 62", result["endereco"])
        self.assertTrue(result["endereco"].endswith("CEP 04634-042"))

    def test_currency_without_ptax_uses_the_closing_file(self):
        from jurisync.workflows.petitions import cotacao

        response = mock.Mock(status_code=200, text="19/10/2026;715;A;CLP;0,00514900;0,00515600;980,7;981,9\n")
        with mock.patch.object(cotacao.requests, "get", return_value=response):
            result = cotacao.quote("CLP", "2026-10-19")
        self.assertEqual(result["cotacao"], "0,00515600")
        self.assertIn("fechamento", result["fonte"])
