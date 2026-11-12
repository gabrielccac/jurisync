"""Projudi "tjpi-publico" generation (TJPI): placeholder, not wired.

Projudi 1.9.8 (2011) on Tomcat 6: local form ``POST /projudi/publico/Logon`` (``login``/``senha``), CAPTCHA on
public consultation, ``DownloadArquivo?arquivo=`` for files. The system is legacy and the court points to its
deactivation for new distributions: confirm it still accepts new users before investing here.

Nothing here was validated against a real session, so these portals stay ``wired=False`` in ``portals.py`` and this
adapter refuses to run. TJGO's ``/BuscaProcesso`` and ``/MovimentacaoArquivo`` routes are not reused. Wire one by
implementing the ``Adapter`` methods and setting ``wired=True`` only after a live smoke proves login + process + one
document. See ``docs/connectors/projudi-mapping.md``.
"""

from jurisync.connectors.projudi import base

NOT_VALIDATED = "Projudi tjpi-publico (TJPI) is not validated yet; only tjgo-1g is wired"


class TjpiAdapter(base.Adapter):
    flavor = "tjpi-publico"

    def _refuse(self, *_args, **_kwargs):
        raise NotImplementedError(NOT_VALIDATED)

    login = refresh = is_login_redirect = open_process = fetch_index = movement_files = download_url = _refuse


ADAPTER = TjpiAdapter()
