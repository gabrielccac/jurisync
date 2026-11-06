"""Projudi "legacy-local" generation (TJAM): placeholder, not wired.

Local form at ``/projudi/usuario/logon.do`` (``login``/``senha``). The Projudi certificate is internal to the
system (signing), not an A1 over mutual TLS. Public consultation is behind F5/reCAPTCHA; authenticated search, movement and
document routes are not mapped (``arquivo.do?id=`` was seen, protected).

Nothing here was validated against a real session, so these portals stay ``wired=False`` in ``portals.py`` and this
adapter refuses to run. TJGO's ``/BuscaProcesso`` and ``/MovimentacaoArquivo`` routes are not reused. Wire one by
implementing the ``Adapter`` methods and setting ``wired=True`` only after a live smoke proves login + process + one
document. See ``docs/connectors/projudi-mapping.md``.
"""

from jurisync.connectors.projudi import base

NOT_VALIDATED = "Projudi legacy-local (TJAM) is not validated yet; only tjgo-1g is wired"


class TjamAdapter(base.Adapter):
    flavor = "legacy-local"

    def _refuse(self, *_args, **_kwargs):
        raise NotImplementedError(NOT_VALIDATED)

    login = refresh = is_login_redirect = open_process = fetch_index = movement_files = download_url = _refuse


ADAPTER = TjamAdapter()
