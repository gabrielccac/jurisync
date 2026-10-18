---
name: office-pje-trf1-filing
description: Prepare and create an office BPC initial filing as a TRF1 1G PJe draft (SJAP by default) with the filing and PJe MCP tools, following the office's "[IT] Protocolo de Iniciais BPC - PJE". Drafts only; never Protocolar.
---

# Office: BPC initial filing on PJe TRF1 1º grau

Use `profile: "jean"` in every filing tool. The profile already sets what the office guide fixes: matéria
`DIREITO PREVIDENCIÁRIO`, jurisdição `Seção Judiciária do Amapá`, classe `PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)`,
assunto `11946` (Pessoa com Deficiência), INSS as RÉU, justiça gratuita sim, tutela sim, segredo não, Juízo 100%
Digital solicitado and priority `Pessoa com deficiência`. The attorney is the user logged in to PJe: do not add
`attorneys` unless the user names an extra one.

1. Gather the documents into one local folder (from Drive, GERID or ADVBOX) with a `sources.json` declaring each
   file's origin (see the input contract in `docs/connectors/pje-trf5-filing.md`).
2. Facts carry only the case: `authorCpf`, `author.name` (as in PJe), optional `author.address`, `characteristics.claimValue`.
   Override only what differs: `classification.jurisdiction` for a client in another state, `subject`/`subjects` and
   `characteristics.priority` (e.g. `["Idoso(a)"]` with subject 11947) for an elderly BPC.
   A minor or represented author needs `author.representative {cpf, name, kind, address {cep, numero}}`.
3. `filing_prepare` with `sourceFolder`, then `filing_review`, `filing_finalize` (types come out in TRF1 labels:
   Petição inicial, Procuração, Comprovante de residência, Cadastro Nacional de Informações Sociais - CNIS for the
   CadÚnico summary, Atestado médico...). Show the summary; only after the user explicitly approves, `filing_approve`.
4. TRF1 steps after Incluir have not run live yet (`unverifiedSteps`). Until the first supervised filing has passed,
   run `pje_draft_write` one stage at a time with the user watching PJe, in this order, and have the user confirm each
   in the portal: `create`, `subject`, `author`, `public-bodies`, `characteristics`, `documents`, then
   `pje_draft_status` with `checkPje: true`. Follow `docs/connectors/pje-trf1-filing-recon.md` (test plan).
5. A legal representative is a manual step: `pje_draft_status` lists name, CPF, kind and address; the user adds it in
   the Partes tab before protocol. Never protocol: the user does Protocolar.
