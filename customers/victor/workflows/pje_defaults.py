"""Victor's office choices verified in the TRF5 mock draft. Case facts stay explicit."""

import os

UNCERTAIN_SUPPORTING_TYPE = "Outros Documentos"


def victor_defaults():
    return {
        "portal": "trf5-1g",
        "attorney": {
            "cpf": os.environ.get("VICTOR_PJE_GRAZIELE_CPF") or os.environ.get("VICTOR_PJE_ATTORNEY_CPF") or "",
            "name": "GRAZIELE ALVES DA SILVA",
            "oabNumber": "48647",
            "oabState": "PE",
        },
        "passive": {"search": "inss", "cnpj": "29979036000140", "name": "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"},
        "otherParticipants": [
            {"role": "ÓRGÃO DE CUMPRIMENTO", "search": "ceab", "cnpj": "29979036001465", "name": "CEAB-DJ INSS"},
            {
                "role": "FISCAL DA LEI",
                "search": "ministério público",
                "cnpj": "26989715005414",
                "name": "MINISTERIO PUBLICO DA UNIAO",
            },
        ],
        "characteristics": {"legalAid": True, "injunction": False, "priority": "Juízo 100% Digital"},
    }
