"""Tool names: `<portal>_<verb>_<object>` with the closed verbs below, and the old names kept as aliases.

An alias is accepted in `tools/call` (local and hosted) and never listed in `tools/list`. Removing one is Gabriel's
call (docs/padroes.md, section 3).
"""

VERBS = (
    "get", "list", "search", "read", "download", "calculate", "create", "update", "delete", "upload", "complete",
    "fill", "verify", "save", "mark",
)  # fmt: skip

# Old name -> current name.
ALIASES = {
    "pje_list_inbox": "pje_list_intimacoes",
    "pje_list_expediente_counters": "pje_get_counters",
    "pje_draft_write": "pje_fill_draft",
    "pje_draft_delete": "pje_delete_draft",
    "pje_draft_status": "pje_get_draft_status",
    "pje_draft_inspect": "pje_get_draft",
    "eproc_list_notices": "eproc_list_intimacoes",
    "projudi_list_intimations": "projudi_list_intimacoes",
    "stj_list_inbox": "stj_list_intimacoes",
    "stj_list_publications": "stj_list_publicacoes",
    "jpe_list_communications": "jpe_list_intimacoes",
    "djen_search_communications": "djen_search_comunicacoes",
    "djen_get_certidao_text": "djen_read_certidao",
    "advbox_activity_details": "advbox_get_activity",
    "advbox_lawsuit_report": "advbox_get_lawsuit_report",
    "advbox_task_filters": "advbox_list_task_filters",
    "advbox_workflow_tasks": "advbox_list_workflow_tasks",
    "advbox_ensure_session": "advbox_get_session",
    "advbox_list_publications": "advbox_list_publicacoes",
    "juridiq_find_lawsuit": "juridiq_search_lawsuits",
    "juridiq_list_publications": "juridiq_list_publicacoes",
    "juridiq_list_pending_publications": "juridiq_list_pending_publicacoes",
    "juridiq_get_publication": "juridiq_get_publicacao",
    "juridiq_mark_publications_read": "juridiq_mark_publicacoes_read",
}


def canonical(name):
    """The current name of a tool called by an old one; any other name unchanged."""
    return ALIASES.get(name, name)
