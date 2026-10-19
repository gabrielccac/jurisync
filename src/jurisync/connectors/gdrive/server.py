"""Google Drive MCP server (read-only service account): export a shared folder for filing.

Separate from the filing server on purpose: the filing only knows its input contract, and this server is one of the
producers of that input, like the ADVBOX exporter. It needs a Google service-account key (see docs/connectors/gdrive.md)
and never writes to Drive.
"""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from jurisync.connectors.gdrive import filing_folder
from jurisync.connectors.gdrive.auth import DriveAuthError
from jurisync.connectors.gdrive.client import DriveError
from jurisync.core.mcp import READ_ONLY, load_env_file, serve, tool_errors

ERRORS = (ValueError, RuntimeError, OSError, KeyError, DriveAuthError, DriveError)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
FolderRef = Annotated[str, Field(min_length=10, max_length=500)]

server = MCPServer("gdrive", version="1.0.0")


@server.tool(
    name="drive_export_filing_folder",
    title="Export Google Drive documents for a PJe filing",
    description="Download Google Drive documents shared with the jurisync service account (read-only) into a new local input folder for pje_filing_prepare; works in Claude Chat, no terminal needed. `folder` is the link (or id) of the client's documents folder; `files` adds single file links from elsewhere, such as the petition kept in another folder. Google Docs/Sheets/Slides are exported to PDF, Drive's md5 is checked, and a sources.json records each file's Drive origin (id, folder, original name, modification time, link). Subfolders are skipped unless includeSubfolders is true; `exclude` takes name or path globs (e.g. a protocol receipt). Files over maxFileMb fail the export. Returns `sourceFolder`: pass it unchanged to pje_filing_prepare. If the account cannot see a folder or file, the error names the address to share it with (Viewer). Writes only that local folder; never writes to Drive.",
    annotations=LOCAL_WRITE,
)
def export_filing_folder(
    folder: FolderRef | None = None,
    files: Annotated[list[FolderRef], Field(max_length=50)] = [],
    includeSubfolders: bool = False,
    exclude: Annotated[list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=50)] = [],
    maxFileMb: Annotated[float, Field(gt=0, le=500)] = filing_folder.DEFAULT_MAX_MB,
) -> dict[str, Any]:
    with tool_errors("Google Drive", *ERRORS):
        if not folder and not files:
            raise ValueError("Give the Drive folder link, file links, or both")
        label = filing_folder.folder_id(folder) if folder else filing_folder.drive_id(files[0], "file")
        client = filing_folder.make_client()
        return filing_folder.export_filing_folder(
            client,
            folder,
            filing_folder.default_destination(label),
            files=tuple(files),
            include_subfolders=includeSubfolders,
            exclude=tuple(exclude),
            max_mb=maxFileMb,
        )


@server.tool(
    name="drive_check_access",
    title="Check the Google Drive service account",
    description="Test the service-account key (gets a read-only token) and, when `folder` is given (link or id), that the folder is shared with the account. Returns the account's e-mail: share each client folder (or the shared drive) with that address as Viewer (Leitor). Without a key, the error says where to save it.",
    annotations=READ_ONLY,
)
def check_access(folder: FolderRef | None = None) -> dict[str, Any]:
    with tool_errors("Google Drive", *ERRORS):
        return filing_folder.check_access(filing_folder.make_client(), folder)


def main():
    load_env_file()
    serve(server)


if __name__ == "__main__":
    main()
