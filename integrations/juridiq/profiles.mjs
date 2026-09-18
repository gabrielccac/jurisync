// Client policy is data supplied to the orchestrator; the pipeline mechanics
// remain shared. Template titles are resolved against the live account.

export const DEFAULT_PROFILE = {
  id: "jean-ferreira-bpc",
  language: "pt-BR",
  skillReferences: [
    ".claude/skills/juridiq-publication-triage/SKILL.md",
    ".claude/skills/juridiq-publication-triage/references/core-rules.md",
    ".claude/skills/juridiq-publication-triage/references/orchestration-contract.md",
    ".claude/skills/juridiq-publication-triage/references/classification-rules.md",
    ".claude/skills/juridiq-publication-triage/references/task-models.md",
    ".claude/skills/juridiq-publication-triage/references/pje-enrichment.md",
  ],
  taskTemplates: {
    replica_bpc: "Réplica BPC - (Nome Cliente)",
    aceitacao_acordo: "Manifestação Aceitação Acordo - (Nome Cliente)",
    manifestacao: "Manifestação - (Nome Cliente)",
    pericia_medica: "Comunicação Perícia Médica Federal - (Nome Cliente)",
    pericia_social: "Comunicação Perícia Social - (Nome do Cliente)",
    remarcacao_pericia: "Remarcação Perícia Judicial - (Nome Cliente) ",
    impugnacao_laudo: "Impugnação ao Laudo - (Nome Cliente)",
    laudo_favoravel: "Manifestação Laudo Favorável - (Nome do Cliente) ",
    sentenca_favoravel: "Comunicação Sentença Favorável - (Nome Cliente)",
    acordao_favoravel: "Comunicação Acórdão Favorável - (Nome Cliente)",
    rpv: "Expedição RPV - (Nome Cliente)",
    recurso_inominado: "Recurso Inominado BPC - (Nome Cliente)",
    apelacao_bpc: "Apelação BPC - (Nome do Cliente)",
    apelacao_ms: "Apelação MS - (Nome Cliente)",
    embargos_declaracao: "Embargos de Declaração - (Nome Cliente)",
    inicial_bpc: "Inicial BPC - (Nome Cliente)",
    protocolar_inicial_bpc: "Protocolar Inicial BPC - (Nome Cliente)",
  },
  classification: {
    rulesReference: ".claude/skills/juridiq-publication-triage/references/classification-rules.md",
    unknownAction: "review_and_leave_unread",
    noActionNeedsEvidence: true,
    useProvisionalTasks: true,
  },
  eventPolicy: {
    enabled: true,
    duplicateCheck: "indirect_task_process_date",
  },
};
