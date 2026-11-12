"""``python -m jurisync.workflows.petitions check|plan|render``: the steps a collaborator or an agent runs in order."""

import argparse
import json
import sys
from pathlib import Path

from jurisync.workflows.petitions import compose, preflight, render
from jurisync.workflows.petitions.model import PetitionModel


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jurisync.workflows.petitions")
    parser.add_argument("step", choices=("check", "plan", "render"))
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--dossie", required=True)
    parser.add_argument("--autor", type=int, default=1, help="Passenger position in the dossier, starting at 1")
    parser.add_argument("--seguir-com-pendencias", metavar="NOME", help="Who decided to go on with the open items")
    parser.add_argument("--minuta", help="JSON {bloco: texto} with the drafted blocks (render)")
    parser.add_argument("--saida", help="Output .docx (render)")
    parser.add_argument("--modelo-word", help="Office .docx template (render)")
    parser.add_argument("--rodape", help="Footer text (render)")
    args = parser.parse_args(argv)

    model = PetitionModel.load(args.modelo)
    dossier = _load(args.dossie)
    author = args.autor - 1
    if not 0 <= author < len(dossier.get("autores") or []):
        print(f"O dossiê não tem o autor {args.autor}.")
        return 2
    if args.step == "check":
        report = preflight.check(model, dossier, author)
        print(preflight.report_text(report))
        return 0 if report["completo"] else 2
    try:
        drafting_plan = compose.plan(model, dossier, author, confirmed_by=args.seguir_com_pendencias)
    except compose.NotReady as error:
        print(error)
        return 2
    if args.step == "plan":
        print(json.dumps(drafting_plan, ensure_ascii=False, indent=1))
        return 0
    assembled = compose.assemble(model, dossier, drafting_plan, _load(args.minuta) if args.minuta else {})
    if not assembled["ok"]:
        print("Auditoria reprovada:")
        print("\n".join(f"- {problem}" for problem in assembled["problemas"]))
        return 3
    docx_path = render.to_docx(
        assembled, model.section_titles, args.saida, template=args.modelo_word, footer=args.rodape
    )
    print(
        json.dumps(
            {
                "docx": str(docx_path),
                "pdf": str(render.to_pdf(docx_path)),
                "pendencias": assembled["pendencias"],
                "revisao": assembled["revisao"],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
