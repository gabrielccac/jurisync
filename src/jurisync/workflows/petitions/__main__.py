"""``python -m jurisync.workflows.petitions calcular|auditar|gravar``: the tools the drafter uses.

The drafter (a language model following the office's guide) writes the whole petition. These commands only
calculate amounts, audit the finished draft and write it into the office's Word template.
"""

import argparse
import json
import sys
from pathlib import Path

from jurisync.workflows.petitions import auditoria, cadastro, calc, cotacao, render, voo
from jurisync.workflows.petitions.case import AuthorCase


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _group(value, dossier):
    try:
        group = [int(position) - 1 for position in str(value).split(",")]
    except ValueError as error:
        raise ValueError(f"--autor deve ser a posição do autor, ou posições separadas por vírgula: {value}") from error
    for position in group:
        if not 0 <= position < len(dossier.get("autores") or []):
            raise ValueError(f"O dossiê não tem o autor {position + 1}.")
    return group


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jurisync.workflows.petitions",
        description="voo: registro oficial do voo na ANAC (VRA). cnpj: qualificação da ré pela Receita. cep: município do autor. cotacao: câmbio oficial do dia (PTAX) ou do DES (PTAX × FMI). calcular: valores do dossiê (valor da causa, atrasos, DES, dobro, teto). auditar: confere a minuta. "
        "gravar: audita e, aprovada, grava no timbrado (.docx) e gera o PDF.",
        epilog="Campos lidos do dossiê: processo.valor_moral, processo.data_peca, processo.des_cotacao*, "
        "processo.des_quantidade, peca.usar_tese_pretericao, peca.repeticao_em_dobro, escritorio.teto_juizado, "
        "viagem.internacional, viagem.numero_passageiros, viagem.data_*/horario_*/hora_comunicacao, "
        "autores[].autor.despesas[{valor, pago_a}], terceiros, seguir_com_pendencias, pendencias_abertas, "
        "citacoes_novas, documentos_a_juntar. Despesa: {descricao, valor, data, documento, pago_a (re|terceiro), "
        "taxas, reembolsado, pago_por; em moeda estrangeira: valor_original, moeda, cotacao}.",
    )
    parser.add_argument("passo", choices=("calcular", "auditar", "gravar", "cotacao", "voo", "cnpj", "cep"))
    parser.add_argument("--dossie", help="Dados do caso (JSON)")
    parser.add_argument("--moeda", help="cotacao: XDR (DES), USD, EUR, GBP...")
    parser.add_argument("--data", help="cotacao/voo: data aaaa-mm-dd (voo, despesa ou peça)")
    parser.add_argument("--cia", help="voo: companhia, código do bilhete (AD, G3, LA) ou ICAO (AZU, GLO, TAM)")
    parser.add_argument("--numero", help="voo: número do voo, sem a sigla da companhia")
    parser.add_argument("--origem", help="voo: aeroporto de origem, ICAO (SBGR), opcional")
    parser.add_argument("--cnpj", help="cnpj: CNPJ da ré, para a qualificação")
    parser.add_argument("--cep", help="cep: CEP do autor, para o município e o juizado")
    parser.add_argument("--autor", default="1", help="Posição do autor, a partir de 1; ação conjunta: 1,2")
    parser.add_argument("--minuta", help="Minuta em texto (auditar, gravar)")
    parser.add_argument("--referencias", nargs="*", default=[], help="Guias, mapeamento e textos de referência")
    parser.add_argument(
        "--referencias-caso",
        nargs="*",
        default=[],
        help="Textos das petições do escritório lidas para este caso: citação só achada nelas vai para conferência",
    )
    parser.add_argument("--imagens", help="Pasta de onde as imagens são lidas. Padrão: a pasta do dossiê")
    parser.add_argument("--timbrado", help="Modelo Word do escritório (gravar)")
    parser.add_argument("--saida", help="Arquivo .docx de saída (gravar)")
    args = parser.parse_args(argv)
    try:
        return _run(args, parser)
    except ValueError as error:  # a case value that cannot be used: say which, without a traceback
        print(error)
        return 2


def _run(args, parser):
    if args.passo in ("cnpj", "cep"):
        value = args.cnpj if args.passo == "cnpj" else args.cep
        if not value:
            parser.error(f"{args.passo} precisa de --{args.passo}")
        try:
            result = cadastro.company(value) if args.passo == "cnpj" else cadastro.postal_code(value)
        except cadastro.RegistryError as error:
            print(error)
            return 4
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if args.passo == "voo":
        if not (args.cia and args.numero and args.data):
            parser.error("voo precisa de --cia, --numero e --data")
        try:
            records = voo.find(args.cia, args.numero, args.data, origin=args.origem)
        except voo.FlightError as error:
            print(error)
            return 4
        print(json.dumps(records, ensure_ascii=False, indent=1) if records else "Voo não encontrado no VRA da ANAC.")
        return 0 if records else 4
    if args.passo == "cotacao":
        if not (args.moeda and args.data):
            parser.error("cotacao precisa de --moeda e --data")
        try:
            print(json.dumps(cotacao.quote(args.moeda, args.data), ensure_ascii=False, indent=1))
        except cotacao.QuoteError as error:
            print(error)
            return 4
        return 0
    if not args.dossie:
        parser.error(f"{args.passo} precisa de --dossie")
    dossier = _load(args.dossie)
    group = _group(args.autor, dossier)
    if args.passo == "calcular":
        case = AuthorCase(dossier, group[0], group=group)
        calc.apply(case)
        if case.calculated.get("des_sem_cotacao"):
            case.calculated["aviso"] = "Valor da causa provisório: o DES pedido não entrou porque falta a cotação."
        print(json.dumps(case.calculated, ensure_ascii=False, indent=1))
        return 0
    if not args.minuta:
        parser.error(f"{args.passo} precisa de --minuta")
    image_root = Path(args.imagens) if args.imagens else Path(args.dossie).resolve().parent
    report = auditoria.audit(
        Path(args.minuta).read_text(encoding="utf-8"),
        dossier,
        group,
        auditoria.references_text(args.referencias),
        image_root,
        auditoria.references_text(args.referencias_caso),
    )
    items = report.pop("itens")
    if not report["ok"] or args.passo == "auditar":
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ok"] else 3
    if not args.saida:
        parser.error("gravar precisa de --saida <arquivo.docx>")
    docx_path = render.to_docx(
        items,
        args.saida,
        template=args.timbrado,
        image_root=image_root,
        open_items=(dossier.get("pendencias_abertas") or [])
        + [
            {"item": f"Juntar: {item.get('item')}", "quem": item.get("quem") or "cliente"}
            if isinstance(item, dict)
            else {"item": f"Juntar: {item}", "quem": "cliente"}
            for item in dossier.get("documentos_a_juntar") or []
        ],
        confirmed_by=dossier.get("seguir_com_pendencias"),
    )
    report["docx"] = str(docx_path)
    try:
        report["pdf"] = str(render.to_pdf(docx_path))
    except (RuntimeError, ValueError) as error:  # the .docx is the deliverable; say why there is no PDF
        report["pdf_erro"] = str(error)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
