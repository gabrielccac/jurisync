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
    write_tools: frozenset = frozenset()  # the tools that grant opens
    group: str = ""  # hosted connector it is served in ("tribunais"); empty: its own, at /<name>/mcp

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


# Court systems and DJEN: one hosted connector, so an office adds one URL for every court it has. A future court write
# declares write_grant/write_tools like ADVBOX and stays hidden and refused there for an office without the grant.
COURTS = "tribunais"
CONNECTORS = {
    connector.name: connector
    for connector in (
        Connector(
            "pje",
            "PJe",
            attribute="server",
            group=COURTS,
            # The filing run folders these read are on the computer that wrote the drafts; writes stay local too, and
            # so does the draft search of the same TRF5 filing workflow.
            local_only_tools=frozenset(
                {
                    "pje_draft_write",
                    "pje_draft_delete",
                    "pje_draft_status",
                    "pje_draft_inspect",
                    "pje_search_pending_drafts",
                }
            ),
        ),
        Connector("eproc", "Eproc", group=COURTS),
        Connector("esaj", "e-SAJ", group=COURTS),
        Connector("projudi", "Projudi", group=COURTS),
        Connector("stj", "STJ", group=COURTS),
        Connector("tucujuris", "Tucujuris", group=COURTS),
        Connector("jpe", "JPe", group=COURTS),
        Connector("djen", "DJEN", attribute="server", close=None, group=COURTS),
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
                    "advbox_update_lawsuit_stage",
                }
            ),
        ),
        Connector(
            "juridiq",
            "Juridiq",
            close=None,
            login=("JURIDIQ_EMAIL", "JURIDIQ_PASSWORD"),
            write_grant="JURIDIQ_ALLOW_WRITES",
            # Registered only with allow_writes=True; a test compares both builds with this list.
            write_tools=frozenset(
                {
                    "juridiq_create_event",
                    "juridiq_create_lawsuit",
                    "juridiq_create_tag",
                    "juridiq_create_task",
                    "juridiq_create_task_template",
                    "juridiq_delete_lawsuit",
                    "juridiq_delete_tag",
                    "juridiq_delete_task",
                    "juridiq_delete_task_template",
                    "juridiq_mark_publications_read",
                    "juridiq_update_lawsuit",
                    "juridiq_update_tag",
                    "juridiq_update_task",
                    "juridiq_update_task_template",
                }
            ),
        ),
    )
}
SERVICES = tuple(name for name, connector in CONNECTORS.items() if connector.login)
GROUPS = {COURTS: tuple(name for name, connector in CONNECTORS.items() if connector.group == COURTS)}


def endpoints(names):
    """The hosted paths an office with these connectors adds: its group once, then each ungrouped one."""
    paths = []
    for name in names:
        group = CONNECTORS[name].group if name in CONNECTORS else ""
        path = f"/{group or name}/mcp"
        if path not in paths:
            paths.append(path)
    return paths


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
