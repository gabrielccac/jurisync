"""Projudi "legacy-keycloak" generation (TJPR, TJRR): placeholder, not wired.

Both log in through a Keycloak SSO (OAuth code flow) with CPF/CNPJ + password + TOTP: TJPR at
``sso.projudi.tjpr.jus.br`` (realm projudi, client projudi; its A3 button goes to ``projudicrt.tjpr.jus.br`` over mTLS)
and TJRR at ``keycloak.tjrr.jus.br`` (realm projudi, client projudi-client; no certificate login). The adapter is meant to
be configured per portal (SSO host, realm, client), not copied. Public consultation sits behind reCAPTCHA/captcha and is
no fallback; TJRR also restricts access by geography.

Nothing here was validated against a real session, so these portals stay ``wired=False`` in ``portals.py`` and this
adapter refuses to run. TJGO's ``/BuscaProcesso`` and ``/MovimentacaoArquivo`` routes are not reused. Wire one by
implementing the ``Adapter`` methods and setting ``wired=True`` only after a live smoke proves login + process + one
document. See ``docs/connectors/projudi-mapping.md``.
"""

from jurisync.connectors.projudi import base

NOT_VALIDATED = "Projudi legacy-keycloak (TJPR, TJRR) is not validated yet; only tjgo-1g is wired"


class KeycloakAdapter(base.Adapter):
    flavor = "legacy-keycloak"

    def _refuse(self, *_args, **_kwargs):
        raise NotImplementedError(NOT_VALIDATED)

    login = refresh = is_login_redirect = open_process = fetch_index = movement_files = download_url = _refuse


ADAPTER = KeycloakAdapter()
