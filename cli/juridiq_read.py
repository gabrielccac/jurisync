"""Print one Juridiq API response unchanged for schema inspection. Read-only.

python cli/juridiq_read.py --path /user/me
python cli/juridiq_read.py --path /generic-tables --body-file data/query.json
"""

import argparse
import json
import sys
from pathlib import Path

from jurisync.connectors.juridiq.api import JuridiqApi, JuridiqError
from jurisync.core.mcp import load_env_file


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", required=True, help="API path starting with /")
    parser.add_argument("--body-file", type=Path, help="JSON body; only allowed for /generic-tables")
    args = parser.parse_args()
    if not args.path.startswith("/") or args.path.startswith("//"):
        parser.error("--path must be an API path")
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    method, body = "GET", None
    if args.body_file:
        if args.path != "/generic-tables":
            parser.error("POST queries are restricted to /generic-tables")
        method, body = "POST", json.loads(args.body_file.read_text(encoding="utf-8"))
    try:
        response = JuridiqApi().request(args.path, method, body)
    except (JuridiqError, PermissionError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"HTTP {response.status_code}", file=sys.stderr)
    try:
        print(json.dumps(json.loads(response.text), ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text)
    return 0 if 200 <= response.status_code < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
