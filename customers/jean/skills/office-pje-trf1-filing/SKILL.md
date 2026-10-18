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

1. Get the input folder. For the client's Google Drive folder, call `drive_export_filing_folder` with the folder's
   URL or id (`includeSubfolders: true` when documents sit in subfolders, `exclude` for drafts or duplicates); it
   downloads everything to disk, exports Google Docs to PDF, writes `sources.json` and returns `sourceFolder`. If it
   says the folder is not shared, ask the user to share it as Viewer with the service-account e-mail it names
   (`drive_check_access` shows it). Never use the Claude Drive connector for this: it cannot save files to disk.
   For documents from other sources, save them in one local folder with a `sources.json` (input contract in
   `docs/connectors/pje-trf5-filing.md`).
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

## TRF1 2º grau

For an originating action at the 2nd degree (e.g. Agravo de Instrumento in the SJAP/SJPA Turmas Recursais), use
`profile: "jean-trf1-2g"`. The facts must carry `classification.judicialClass` (e.g. `AGRAVO DE INSTRUMENTO (202)`),
`classification.referenceProcess` (the CNJ number of the case of origin, when the class asks for it), `subject`, and
`partyRoles` with the PJe "Tipo da Parte" labels for the class (e.g. `{"active": "AGRAVANTE", "passive": "AGRAVADO"}`;
if they are wrong, the author stage fails listing the options PJe offers). Nothing after Incluir has run on the 2nd
degree yet: run the stages one at a time with the user watching, as on the first 1st-degree filing.
