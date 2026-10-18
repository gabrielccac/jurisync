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

Everything runs through the PJe MCP server, which also offers the filing tools (`filing_*`).

1. Get the input folder: one local folder with the case's PDFs (petition plus attachments) and a `sources.json`
   declaring each file's origin (input contract in `docs/connectors/pje-trf5-filing.md`).
   - Office Drive: the petition is in "1. Iniciais - BPC (Controladoria)" and the documents in the client's folder under
     "Documentos Clientes BPC" (the subfolder with the protocol package when there is one; leave out any protocol
     receipt). With the Drive service account set up, `drive_export_filing_folder` downloads a folder to disk and writes
     `sources.json` itself. In a session with a shell, Claude's own Drive connector also works: `download_file_content`
     saves each file as base64 to a tool-result file; decode it into the folder, check the size Drive reports, and write
     `sources.json` with `jurisync.workflows.filing.contract.write_sources`.
2. Facts carry only the case: `authorCpf`, `author.name` (as in PJe), optional `author.address`, `characteristics.claimValue`.
   Override only what differs: `classification.jurisdiction` for a client in another subsection or state (e.g.
   `Subseção Judiciária de Itaituba-PA`), `subject`/`subjects` and `characteristics.priority` (e.g. `["Idoso(a)"]` with
   subject 11947) for an elderly BPC. A minor or represented author needs
   `author.representative {cpf, name, kind, address {cep, numero}}`. `filing_prepare` lists the claim values and CPFs
   the petition states in `candidates`: use them, and ask when they conflict with the facts.
3. `filing_prepare` (profile, runId, facts, `sourceFolder`) → `filing_review` → `filing_finalize` if anything changes
   (types come out in TRF1 labels: Petição inicial, Procuração, Comprovante de residência, Cadastro Nacional de
   Informações Sociais - CNIS for the CadÚnico summary, Atestado médico, Carta de Indeferimento, Cédula de
   Identidade...). Show the user the summary; only after they explicitly approve it, `filing_approve`.
4. `pje_draft_write` with `stage: create-fill` (refused without that approval), then `pje_draft_status` until it is
   complete, and finally `pje_draft_status` with `checkPje: true`. Return the draft link on the "Protocolar Inicial" tab:
   `https://pje1g.trf1.jus.br/pje/Processo/update.seam?idProcesso=<draftId>&tab=informativo`. If a stage fails, report
   the error; rerunning `fill` skips what is already done.
5. A legal representative is a manual step: `pje_draft_status` lists name, CPF, kind and address; the user adds it in
   the Partes tab before protocol. Never protocol: the user does Protocolar.
