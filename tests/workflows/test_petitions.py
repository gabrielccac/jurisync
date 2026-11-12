import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from docx import Document

from jurisync.workflows.petitions import calc, compose, preflight, render
from jurisync.workflows.petitions.expressions import ExpressionError, evaluate
from jurisync.workflows.petitions.model import PetitionModel


def block(identifier, section, mode, text, condition="sempre", **extra):
    return {
        "id": identifier,
        "secao": section,
        "modo": mode,
        "texto": text,
        "quando_usar": {"expressao": condition},
        "status": "proposto",
        **extra,
    }


MODEL = {
    "citacoes": [{"id": "resp", "referencia": "REsp 1.000.000/GO"}],
    "cenarios": [
        {
            "id": "atraso",
            "condicao": 'peca.cenario == "atraso"',
            "sequencia": ["end", "fatos", "dir", "dir_menor", "valor"],
        }
    ],
    "secoes": [
        {
            "id": "enderecamento",
            "titulo": "Endereçamento",
            "blocos": [block("end", "enderecamento", "campos", "AO JUÍZO DE {{processo.comarca}}")],
        },
        {
            "id": "fatos",
            "titulo": "Dos Fatos",
            "blocos": [
                block(
                    "fatos",
                    "fatos",
                    "gerar_narrativa",
                    "{{g.O_autor}} viajou.",
                    fatos_necessarios=["doc.reserva"],
                    estrutura=["Viagem"],
                )
            ],
        },
        {
            "id": "direito",
            "titulo": "Do Direito",
            "blocos": [
                block(
                    "dir",
                    "direito",
                    "adaptar_ao_caso",
                    "Conforme REsp 1.000.000/GO.",
                    imprime_titulo=True,
                    titulo="DA FALHA",
                    citacoes=["resp"],
                ),
                block("dir_menor", "direito", "fixo", "Autor menor.", condition="autor.menor == true"),
            ],
        },
        {
            "id": "valor_causa",
            "titulo": "Do Valor da Causa",
            "blocos": [block("valor", "valor_causa", "campos", "Valor de R$ {{calc.valor_causa}}.")],
        },
    ],
}
FIELDS = [
    {"campo": "peca.cenario", "descricao": "Cenário", "obrigatorio": "sempre", "fonte_provavel": "calculado"},
    {
        "campo": "processo.comarca",
        "descricao": "Comarca",
        "obrigatorio": "sempre",
        "fonte_provavel": "documento:comprovante",
    },
    {
        "campo": "processo.valor_moral",
        "descricao": "Dano moral",
        "obrigatorio": "sempre",
        "fonte_provavel": "escritorio",
    },
    {
        "campo": "autor.menor",
        "descricao": "Autor é menor",
        "obrigatorio": "sempre",
        "fonte_provavel": "documento:identidade",
    },
    {"campo": "autor.genero", "descricao": "Gênero", "obrigatorio": "sempre", "fonte_provavel": "resumo_do_caso"},
    {
        "campo": "doc.reserva",
        "descricao": "Reserva do voo",
        "obrigatorio": "sempre",
        "fonte_provavel": "documento:reserva",
    },
    {
        "campo": "doc.gastos",
        "descricao": "Comprovantes de gastos",
        "obrigatorio": "opcional",
        "fonte_provavel": "documento:gastos",
    },
    {
        "campo": "calc.valor_causa",
        "descricao": "Valor da causa",
        "obrigatorio": "sempre",
        "fonte_provavel": "calculado",
    },
]
GENDERS = {"g.O_autor": {"autor": "O Autor", "autora": "A Autora", "selecionado_por": ["autor.genero", "autor.numero"]}}


def write_model(folder, rules=()):
    folder = Path(folder)
    (folder / "modelo.json").write_text(json.dumps(MODEL), encoding="utf-8")
    (folder / "dossie.json").write_text(json.dumps({"campos": FIELDS}), encoding="utf-8")
    (folder / "generos.json").write_text(json.dumps({"campos": GENDERS}), encoding="utf-8")
    (folder / "pendencias.json").write_text("[]", encoding="utf-8")
    (folder / "regras.json").write_text(json.dumps({"regras": list(rules)}), encoding="utf-8")
    return PetitionModel.load(folder)


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


class TempModel(unittest.TestCase):
    rules = ()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.model = write_model(self.folder, self.rules)


class ExpressionTest(unittest.TestCase):
    def test_unknown_field_is_reported_unless_the_other_side_decides(self):
        values = {"a": True, "b": False}
        self.assertEqual(evaluate("a == true && b == false", values.get), (True, set()))
        self.assertEqual(evaluate("a == true && c == true", values.get), (None, {"c"}))
        self.assertEqual(evaluate("b == true && c == true", values.get), (False, set()))
        self.assertEqual(evaluate("a == true || c == true", values.get), (True, set()))
        self.assertEqual(evaluate("(b == true || c == true) && a == true", values.get), (None, {"c"}))

    def test_null_comparison_and_always(self):
        self.assertEqual(evaluate("sempre", {}.get), (True, set()))
        self.assertEqual(evaluate("c == null", {}.get), (True, set()))
        self.assertEqual(evaluate("c != null", {}.get), (False, set()))

    def test_invalid_condition_is_an_error(self):
        with self.assertRaises(ExpressionError):
            evaluate("a = true", {}.get)


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


class PreflightTest(TempModel):
    def test_missing_data_pauses_and_says_who_resolves(self):
        dossier = complete_dossier()
        del dossier["doc"]["reserva"]
        report = preflight.check(self.model, dossier)
        self.assertFalse(report["completo"])
        self.assertTrue(report["pode_seguir_com_pendencias"])
        self.assertEqual(
            [(item["campo"], item["quem_resolve"]) for item in report["faltas"]],
            [("doc.reserva", "cliente ou triagem")],
        )
        with self.assertRaises(compose.NotReady):
            compose.plan(self.model, dossier)

    def test_someone_can_confirm_going_on_and_the_items_stay_listed(self):
        dossier = complete_dossier()
        del dossier["processo"]["comarca"]
        drafting_plan = compose.plan(self.model, dossier, confirmed_by="Colaborador")
        items = {item["id"]: item for item in drafting_plan["itens"]}
        self.assertEqual(items["end"]["texto"], "AO JUÍZO DE [PENDENTE: Comarca]")
        drafted = {"fatos": "A Autora viajou.", "dir": "Conforme REsp 1.000.000/GO."}
        result = compose.assemble(self.model, dossier, drafting_plan, drafted)
        self.assertTrue(result["ok"], result["problemas"])
        self.assertEqual(result["pendencias"]["confirmado_por"], "Colaborador")
        self.assertEqual(result["pendencias"]["marcadas_no_texto"], ["[PENDENTE: Comarca]"])
        path = render.to_docx(result, self.model.section_titles, self.folder / "pendente.docx")
        document = Document(path)
        text = [paragraph.text for paragraph in document.paragraphs]
        self.assertIn("PENDÊNCIAS DESTA MINUTA (retirar esta página antes do protocolo)", text)
        runs = [run for paragraph in document.paragraphs for run in paragraph.runs if run.text.startswith("[PENDENTE")]
        self.assertTrue(runs and runs[0].font.highlight_color is not None)

    def test_optional_absence_is_complete_and_calculated_total_is_filled(self):
        report = preflight.check(self.model, complete_dossier())
        self.assertTrue(report["completo"], report)
        self.assertEqual(report["blocos"], ["end", "fatos", "dir", "valor"])

    def test_not_held_needs_reason_and_who_recorded(self):
        dossier = complete_dossier()
        del dossier["doc"]["reserva"]
        dossier["nao_possui"] = [{"campo": "doc.reserva"}]
        self.assertFalse(preflight.check(self.model, dossier)["completo"])
        dossier["nao_possui"] = [
            {"campo": "doc.reserva", "motivo": "Cliente não localizou", "registrado_por": "Triagem"}
        ]
        report = preflight.check(self.model, dossier)
        self.assertTrue(report["completo"])
        self.assertTrue(report["alertas"])

    def test_conflict_needs_who_resolved_it(self):
        dossier = complete_dossier()
        dossier["conflitos"] = [{"descricao": "Relato e bilhete divergem", "resolvido": True}]
        self.assertFalse(preflight.check(self.model, dossier)["completo"])
        dossier["conflitos"][0]["resolvido_por"] = "Coordenação"
        self.assertTrue(preflight.check(self.model, dossier)["completo"])

    def test_case_without_a_scenario_uses_the_closest_reference(self):
        dossier = complete_dossier()
        dossier["peca"]["cenario"] = "outro"
        report = preflight.check(self.model, dossier)
        self.assertFalse(report["pode_seguir_com_pendencias"])
        self.assertTrue(report["avisos_fortes"])
        dossier["peca"]["cenario_referencia"] = "atraso"
        report = preflight.check(self.model, dossier)
        self.assertEqual(report["cenario"], "atraso")
        self.assertTrue(any("modelo exato" in item for item in report["alertas"]))

    def test_report_is_readable(self):
        dossier = complete_dossier()
        del dossier["processo"]["comarca"]
        text = preflight.report_text(preflight.check(self.model, dossier))
        self.assertIn("PENDÊNCIAS", text)
        self.assertIn("Comarca", text)


class StrongWarningTest(TempModel):
    rules = ({"se": "autor.menor == false", "tipo": "aviso_forte", "mensagem": "Conferir com a coordenação."},)

    def test_office_rule_pauses_until_someone_confirms(self):
        report = preflight.check(self.model, complete_dossier())
        self.assertEqual(report["avisos_fortes"], ["Conferir com a coordenação."])
        with self.assertRaises(compose.NotReady):
            compose.plan(self.model, complete_dossier())
        self.assertTrue(compose.plan(self.model, complete_dossier(), confirmed_by="Coordenação")["itens"])


class ComposeTest(TempModel):
    def drafted(self):
        return {"fatos": "A Autora viajou.", "dir": "No caso, conforme REsp 1.000.000/GO, houve falha."}

    def test_plan_fills_fields_and_gender_and_keeps_instructions(self):
        items = {item["id"]: item for item in compose.plan(self.model, complete_dossier())["itens"]}
        self.assertEqual(items["fatos"]["texto"], "A Autora viajou.")
        self.assertEqual(items["fatos"]["estrutura"], ["Viagem"])
        self.assertEqual(items["valor"]["texto"], "Valor de R$ 20.100,50.")

    def test_assemble_audits_and_lists_points_for_review(self):
        dossier = complete_dossier()
        result = compose.assemble(self.model, dossier, compose.plan(self.model, dossier), self.drafted())
        self.assertTrue(result["ok"], result["problemas"])
        self.assertEqual(result["revisao"]["citacoes_nao_validadas"], ["resp"])

    def audit(self, **changes):
        dossier = complete_dossier()
        return compose.assemble(self.model, dossier, compose.plan(self.model, dossier), {**self.drafted(), **changes})[
            "problemas"
        ]

    def test_audit_catches_gaps_and_contamination(self):
        self.assertTrue(
            any(
                "sem redação" in item
                for item in compose.assemble(
                    self.model, complete_dossier(), compose.plan(self.model, complete_dossier()), {}
                )["problemas"]
            )
        )
        self.assertTrue(
            any("outro passageiro" in item for item in self.audit(fatos="A Autora viajou com Pessoa Dois."))
        )
        self.assertTrue(
            any("sem preencher" in item for item in self.audit(fatos="A Autora viajou em {{viagem.data}}."))
        )
        self.assertTrue(any("sem preencher" in item for item in self.audit(fatos="A Autora viajou [PREENCHER].")))
        self.assertTrue(any("gênero" in item for item in self.audit(fatos="O Autor viajou.")))

    def test_audit_rejects_invented_citation_and_changed_fixed_text(self):
        self.assertTrue(any("fora do modelo" in item for item in self.audit(dir="Conforme REsp 9.999.999/SP.")))
        self.assertTrue(any("ajuste autorizado" in item for item in self.audit(end="AO JUÍZO DE OUTRA COMARCA")))
        self.assertTrue(any("não previsto" in item for item in self.audit(novo="Texto.")))

    def test_render_docx_with_numbered_sections(self):
        dossier = complete_dossier()
        result = compose.assemble(self.model, dossier, compose.plan(self.model, dossier), self.drafted())
        path = render.to_docx(
            result, self.model.section_titles, self.folder / "saida" / "inicial.docx", footer="Rodapé"
        )
        text = [paragraph.text for paragraph in Document(path).paragraphs]
        self.assertIn("I – DOS FATOS", text)
        self.assertNotIn("I – I", " ".join(text))
        self.assertIn("II – DO DIREITO", text)
        self.assertIn("1. DA FALHA", text)
        self.assertIn("III – DO VALOR DA CAUSA", text)

    def test_audit_allows_elision_and_catches_variants_of_names_and_facts(self):
        self.assertEqual(self.audit(dir="Conforme REsp 1.000.000/GO, [...] houve falha."), [])
        self.assertEqual(self.audit(dir="Conforme REsp nº 1.000.000/GO, houve falha."), [])
        self.assertTrue(any("fora do modelo" in item for item in self.audit(dir="Conforme REsp nº 9.999.999/SP.")))
        self.assertTrue(any("fora do modelo" in item for item in self.audit(dir="Apelação Cível nº 999/2020.")))
        self.assertTrue(any("artigo" in item for item in self.audit(dir="Nos termos do art. 999 do CDC.")))
        self.assertTrue(any("valor" in item for item in self.audit(fatos="A Autora pagou R$ 9.999,00.")))
        self.assertTrue(any("data" in item for item in self.audit(fatos="A Autora viajou em 01/01/2020.")))

    def test_audit_catches_first_name_and_name_without_accent(self):
        dossier = complete_dossier()
        dossier["autores"][1]["autor"]["nome"] = "João Teste"
        drafting_plan = compose.plan(self.model, dossier)
        for text in ("A Autora viajou com João.", "A Autora viajou com Joao Teste."):
            result = compose.assemble(self.model, dossier, drafting_plan, {**self.drafted(), "fatos": text})
            self.assertTrue(any("outro passageiro" in item for item in result["problemas"]))

    def test_office_standard_text_changes_only_with_authorized_adjustment(self):
        dossier = complete_dossier()
        drafting_plan = compose.plan(self.model, dossier)
        base = self.drafted()
        self.assertTrue(any("ajuste autorizado" in item for item in self.audit(end="AO JUÍZO DE OUTRA COMARCA")))
        refused = {**base, "_ajustes_fixos": {"end": {"texto": "AO JUÍZO DA CAPITAL"}}}
        self.assertTrue(
            any("sem motivo" in i for i in compose.assemble(self.model, dossier, drafting_plan, refused)["problemas"])
        )
        accepted = {
            **base,
            "_ajustes_fixos": {
                "end": {"texto": "AO JUÍZO DA CAPITAL", "motivo": "Foro diverso", "autorizado_por": "Ana"}
            },
        }
        result = compose.assemble(self.model, dossier, drafting_plan, accepted)
        self.assertTrue(result["ok"], result["problemas"])
        self.assertEqual(result["itens"][0]["texto"], "AO JUÍZO DA CAPITAL")
        self.assertEqual(result["revisao"]["ajustes_autorizados"], {"end": "Foro diverso"})

    def test_case_can_omit_a_thesis_add_a_topic_and_cite_with_source(self):
        dossier = complete_dossier()
        drafting_plan = compose.plan(self.model, dossier)
        draft = {
            "fatos": "A Autora viajou.",
            "_omitidos": {"dir": "A tese não se aplica aos fatos deste caso."},
            "_extras": [
                {
                    "secao": "direito",
                    "titulo": "DA PROVA",
                    "texto": "Conforme REsp 2.222.222/GO.",
                    "motivo": "Fato próprio do caso",
                }
            ],
            "_citacoes_novas": [{"referencia": "REsp 2.222.222/GO", "fonte": "Enviado pela advogada"}],
        }
        result = compose.assemble(self.model, dossier, drafting_plan, draft)
        self.assertTrue(result["ok"], result["problemas"])
        self.assertNotIn("dir", [item["id"] for item in result["itens"]])
        self.assertEqual(result["revisao"]["topicos_extras"], ["DA PROVA"])
        draft["_citacoes_novas"][0]["fonte"] = ""
        problems = compose.assemble(self.model, dossier, drafting_plan, draft)["problemas"]
        self.assertTrue(any("sem fonte" in item for item in problems))

    def test_pending_mark_needs_confirmation(self):
        self.assertTrue(
            any("sem confirmação" in item for item in self.audit(fatos="A Autora pagou [PENDENTE: valor]."))
        )

    def test_render_refuses_a_failed_audit(self):
        with self.assertRaises(ValueError):
            render.to_docx({"ok": False, "itens": []}, {}, self.folder / "x.docx")


if __name__ == "__main__":
    unittest.main()
