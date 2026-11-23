"""Every connector in one place: the module that serves it, what closes its sessions, its portals or service login,
and the tools the hosted server keeps off or grants per office.

``install.serve``, ``install.setup`` and ``hosted`` read this list instead of keeping their own, so a connector added
here is served, registered, checked and forgotten everywhere.
"""

import importlib
from dataclasses import dataclass
from functools import reduce

from jurisync import portals


@dataclass(frozen=True)
class Connector:
    name: str
    label: str
    attribute: str = "create_server()"  # the module's server, or "create_server()" to build one
    close: str | None = "sessions.close"  # attribute path called on exit
    login: tuple = ()  # account variables of a service without portals (ADVBOX, Juridiq)
    settings: tuple = ()  # other variables of that service an office may set
    local_only_tools: frozenset = frozenset()  # tools that only work on the office's computer
    write_grant: str = ""  # variable that lets an office see and call the connector's writes
    write_tools: frozenset = frozenset()  # empty with a grant: the connector registers them only when allowed

    @property
    def module(self):
        return f"jurisync.connectors.{self.name}.server"

    @property
    def command(self):
        return f"jurisync-{self.name}"

    def portals(self):
        """Its portal keys; empty for a service."""
        return portals.keys(self.name)

    def sessions(self):
        """The connector's session pool."""
        return importlib.import_module(self.module).sessions

    def load(self, **options):
        """``(MCPServer, close callback or None)``; ``options`` go to ``create_server``."""
        module = importlib.import_module(self.module)
        server = getattr(module, self.attribute.removesuffix("()"))
        server = server(**options) if self.attribute.endswith("()") else server
        return server, (reduce(getattr, self.close.split("."), module) if self.close else None)


CONNECTORS = {
    connector.name: connector
    for connector in (
        Connector(
            "pje",
            "PJe",
            attribute="server",
            # The filing run folders these read are on the computer that wrote the drafts; writes stay local too.
            local_only_tools=frozenset(
                {"pje_draft_write", "pje_draft_delete", "pje_draft_status", "pje_draft_inspect"}
            ),
        ),
        Connector("eproc", "Eproc"),
        Connector("esaj", "e-SAJ"),
        Connector("projudi", "Projudi"),
        Connector("stj", "STJ"),
        Connector("tucujuris", "Tucujuris"),
        Connector("jpe", "JPe"),
        Connector("djen", "DJEN", attribute="server", close=None),
        Connector(
            "advbox",
            "ADVBOX",
            attribute="server",
            close="client.close",
            login=("ADVBOX_EMAIL", "ADVBOX_PASSWORD"),
            settings=("ADVBOX_DEVICE_ID", "ADVBOX_UPLOAD_FOLDER"),
            write_grant="ADVBOX_ALLOW_WRITES",
            write_tools=frozenset(
                {
                    "advbox_create_folder",
                    "advbox_upload_files",
                    "advbox_create_task",
                    "advbox_update_task",
                    "advbox_complete_task",
                    "advbox_delete_task",
                }
            ),
        ),
        Connector(
            "juridiq",
            "Juridiq",
            close=None,
            login=("JURIDIQ_EMAIL", "JURIDIQ_PASSWORD"),
            write_grant="JURIDIQ_ALLOW_WRITES",
        ),
    )
}
SERVICES = tuple(name for name, connector in CONNECTORS.items() if connector.login)


def get(name):
    try:
        return CONNECTORS[name]
    except KeyError:
        raise ValueError(f"Unknown connector {name}; known: {', '.join(CONNECTORS)}") from None


def of_portal(key):
    """The connector that serves a portal key."""
    return get(portals.get(key).system)


def label(name):
    return CONNECTORS[name].label if name in CONNECTORS else name
