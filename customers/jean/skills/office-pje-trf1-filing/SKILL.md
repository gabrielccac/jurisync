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

Everything runs through MCP tools, so it works in Claude Chat too (no terminal): the Drive exporter (`jurisync-gdrive`)
and the PJe server (`jurisync-pje`), which also offers the filing tools (`pje_filing_*`). TRF1 1º grau only: there is
no filing on the 2nd degree.

1. Documents: the petition is in "1. Iniciais - BPC (Controladoria)" and the documents in the client's folder under
   "Documentos Clientes BPC" (the subfolder with the protocol package when there is one). Call
   `drive_export_filing_folder` with `folder` = the client's folder link and `files` = [the petition's link], with
   `exclude` for any protocol receipt (e.g. `["*protocolo*"]`) and `includeSubfolders` when needed. If the account
   cannot see something, the error names the e-mail to share it with (Viewer): tell the user. In a session with a
   shell, Claude's own Drive connector also works (`download_file_content` saves base64 to a tool-result file; decode
   it into a folder and write `sources.json` with `jurisync.workflows.filing.contract.write_sources`).
2. Facts carry only the case: `authorCpf`, `author.name` (as in PJe), optional `author.address`, `characteristics.claimValue`.
   Override only what differs: `classification.jurisdiction` for a client in another subsection or state (e.g.
   `Subseção Judiciária de Itaituba-PA`), `subject`/`subjects` and `characteristics.priority` (e.g. `["Idoso(a)"]` with
   subject 11947) for an elderly BPC. A minor or represented author needs
   `author.representative {cpf, name, kind, address {cep, numero}}` (kind `mae`, `pai`, `tutor`, `curador`,
   `procurador` or `outro`); CPFs with wrong check digits are refused. `pje_filing_prepare` lists the claim values and
   CPFs the petition states in `candidates`: use them, and ask when they conflict with the facts.
3. `pje_filing_prepare` (profile, a new runId, facts, `sourceFolder` exactly as the exporter returned it) →
   `pje_filing_review` → `pje_filing_build` (types come out in TRF1 labels: Petição inicial, Procuração, Comprovante de
   residência, Cadastro Nacional de Informações Sociais - CNIS for the CadÚnico summary, Atestado médico, Carta de
   Indeferimento, Cédula de Identidade...; a type TRF1 does not offer is refused here). Show the user the summary; only
   after they explicitly approve it, `pje_filing_approve`.
4. `pje_draft_create` (refused without that approval), then `pje_draft_status` until it is complete, and finally
   `pje_draft_status` with `checkPje: true`. Return the draft link on the "Protocolar Inicial" tab:
   `https://pje1g.trf1.jus.br/pje/Processo/update.seam?idProcesso=<draftId>&tab=informativo`. If a stage fails, report
   the error; `pje_draft_fill` continues and skips what is already done (a timed-out attachment is looked for on the
   draft before it is sent again).
5. A legal representative is linked in the author step ("Procurador | Terceiro Vinculado"). Its steps after Confirmar
   have not run live yet (listed in `unverifiedSteps`): on the first real case, have the user watch. If the link stops,
   `pje_draft_status` lists it as a manual step with name, CPF, kind, address and the reason; the user adds it in the
   Partes tab before protocol. Never protocol: the user does Protocolar.
6. After the user protocols (or discards) the draft, `pje_filing_cleanup` with the `runId` removes the run and the
   exported documents. A draft still in Não Protocolados is kept unless the user explicitly confirms.
