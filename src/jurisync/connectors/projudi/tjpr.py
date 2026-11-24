"""Projudi TJPR: the Keycloak login (``keycloak.py``) in front of the same lawyer desk as TJAM (``tjam.py``).

Observed live on 2026-11-24 (one login, a lawyer account, CPF + password + TOTP):

- Entry: the home page's "Advogados, Partes" option is ``autenticacao.do?_tj=<token>``. Without that token the SSO
  accepts the login and Projudi answers "Erro geral" on the way back, so the link is always taken from ``home.do``.
- After the SSO: a page whose menu is nested ``ul#main-menu`` anchors (TJAM writes ``add_menu`` calls). Sections,
  labels, list pages, desk, search and process pages are TJAM's. Pages mix UTF-8 and ISO-8859-1 text.
- The frameset (``usuario/postLogon.do``) redirects to another SSO when requested on its own: the menu only comes with
  the login, so a session without it logs in again.
- The desk's agenda shows 20 of the intimações awaiting reading: the "Novas" lists of both instances, read to the end,
  say which processes have one.
"""

import re

from jurisync.connectors.projudi import base, legacy, parsers, tjam
from jurisync.connectors.projudi.keycloak import KeycloakAdapter
from jurisync.core.http import TIMEOUT, impersonate_for

ENTRY = re.compile(r"""class="externo"[^>]*location\.href='(/projudi/autenticacao\.do\?_tj=[0-9a-fA-F]+)'""")
NOVAS_PAGES = 50


def menu_entries(html, root):
    """``[(section, label, path)]`` of the ``ul#main-menu`` anchors, with paths relative to the application root."""
    menu = legacy.soup(html).find(id="main-menu")
    entries = []

    def walk(items, sections):
        for item in items.find_all("li", recursive=False):
            anchor, inner = item.find("a", recursive=False), item.find("ul", recursive=False)
            label = tjam.clean(anchor) if anchor else ""
            if inner is not None:
                walk(inner, [*sections, label])
            elif anchor is not None and (anchor.get("href") or "").startswith(root):
                entries.append((" / ".join(sections), label, "/" + anchor["href"].removeprefix(root)))

    if menu is not None:
        walk(menu, [])
    return entries


class TjprAdapter(KeycloakAdapter, tjam.TjamAdapter):
    def _entry_url(self, session, config):
        for path in ("", "home.do"):  # the frameset, then the page it shows
            page = session.get(
                config.url(path), impersonate=impersonate_for(config), allow_redirects=False, timeout=TIMEOUT
            )
        link = ENTRY.search(parsers.decode(page.content))
        if page.status_code != 200 or link is None:
            raise base.ProjudiParseError("Projudi home page has no lawyer login link")
        return config.url(link.group(1).removeprefix(config.root))

    def _logged_in(self, session, config, page):
        entries = menu_entries(parsers.decode(page.content), config.root)
        if not entries:
            raise RuntimeError("Projudi login did not open the lawyer menu")
        state = self._state(session)
        state["entries"] = entries
        state["menu"] = {label: path for _section, label, path in reversed(entries)}

    def refresh(self, portal, session):
        return tjam.TjamAdapter.refresh(self, portal, session)

    def _menu(self, session, portal):
        self._entries(session, portal)
        return self._state(session)["menu"]

    def _entries(self, session, portal):
        entries = self._state(session).get("entries")
        if not entries:
            raise base.ProjudiSessionExpired("Projudi session has no menu")
        return entries

    def _awaiting(self, session, portal):
        numbers, complete = set(), True
        for instance in tjam.INSTANCES:
            section, label = tjam.INTIMATION_LISTS["novas"]
            read = self._read_list(session, portal, section, label, instance=instance, max_pages=NOVAS_PAGES)
            numbers.update(row["processNumber"] for row in read["rows"])
            complete = complete and read["complete"]
        return numbers, complete


ADAPTER = TjprAdapter()
