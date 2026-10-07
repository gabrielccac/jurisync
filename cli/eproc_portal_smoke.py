"""Quick read-only smoke test for the JFSC/JFRS eproc adapter."""

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jurisync import portals
from jurisync.connectors.eproc import client
from jurisync.core.mcp import load_env_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("portal", choices=portals.keys("eproc"))
    parser.add_argument("--process-number", help="Use this case instead of one found in a notice queue")
    parser.add_argument("--read-document", action="store_true", help="Read the first listed document, if present")
    parser.add_argument("--capture-dir", default="data/eproc-smoke")
    args = parser.parse_args()

    os.umask(0o077)
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    capture_dir = Path(args.capture_dir) / (f"{args.portal}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    capture_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    capture_dir.chmod(0o700)
    captures = []
    original_request = client.request

    def capture_request(session, method, url, **kwargs):
        response, body = original_request(session, method, url, **kwargs)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        action = re.sub(r"[^a-zA-Z0-9_-]", "_", query.get("acao", query.get("acao_ajax", ["read"]))[0])
        filename = f"{len(captures) + 1:03d}-{action or 'read'}.html"
        path = capture_dir / filename
        path.write_bytes(body.encode("iso-8859-1", errors="replace"))
        path.chmod(0o600)
        captures.append({"file": filename, "status": response.status_code, "bytes": path.stat().st_size})
        return response, body

    client.request = capture_request
    session = None
    result = {"portal": args.portal, "success": False, "captures": captures}
    try:
        session, panel_html, panel_url = client.authenticate(args.portal)
        panel = client.parse_panel(panel_html, portals.eproc(args.portal).base_url)
        (capture_dir / "000-panel.html").write_text(panel_html, encoding="utf-8")
        (capture_dir / "000-panel.html").chmod(0o600)
        result["panel"] = {
            "quickSearch": bool(panel.get("quickSearch")),
            "queues": sorted((panel.get("queues") or {}).keys()),
        }

        queues = {}
        for name in ("open_deadlines", "pending"):
            response = client.list_notices(
                name, session=session, panel_html=panel_html, panel_url=panel_url, portal=args.portal
            )
            queues[name] = response
            result.setdefault("queues", {})[name] = {
                "success": response.get("success", False),
                "count": len(response.get("items", [])),
                "pagination": response.get("pagination", {}),
                "diagnostics": response.get("diagnostics", []),
            }

        process_number = args.process_number
        if not process_number:
            process_number = next(
                (
                    item.get("numero")
                    for queue in queues.values()
                    for item in queue.get("items", [])
                    if item.get("numero")
                ),
                None,
            )
        if process_number:
            process = client.fetch_process(
                process_number, session=session, panel_html=panel_html, panel_url=panel_url, portal=args.portal
            )
            result["process"] = {
                "success": process.get("success", False),
                "partyCount": len(process.get("parties", [])),
                "eventCount": len(process.get("timeline", [])),
                "documentCount": len(process.get("documentInventory", [])),
                "pagination": process.get("pagination", {}),
                "diagnostics": process.get("diagnostics", []),
            }
            if args.read_document and process.get("success") and process.get("documentInventory"):
                document_id = process["documentInventory"][0].get("id")
                if document_id:
                    docs = client.fetch_process(
                        process_number,
                        scope="page",
                        document_ids=[document_id],
                        session=session,
                        panel_html=panel_html,
                        panel_url=panel_url,
                        portal=args.portal,
                    )
                    item = (docs.get("attachments") or [{}])[0]
                    result["document"] = {
                        "status": item.get("status"),
                        "format": item.get("format"),
                        "bytes": item.get("bytes"),
                        "extractionStatus": item.get("extractionStatus"),
                        "textLength": len(item.get("text", "")),
                    }
                else:
                    result["document"] = {"skipped": "first inventory item has no ID"}
        else:
            result["process"] = {"skipped": "no process in either queue; pass --process-number to test detail"}

        result["captures"] = captures
        result["captureDirectory"] = str(capture_dir)
        result["success"] = (
            bool(result["panel"].get("quickSearch"))
            and all(queue.get("success") for queue in queues.values())
            and ("process" not in result or result["process"].get("success", False) or "skipped" in result["process"])
        )
    finally:
        client.request = original_request
        if session is not None:
            session.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
