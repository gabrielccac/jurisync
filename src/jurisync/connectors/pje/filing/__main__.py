import json
import sys

from jurisync.connectors.pje.filing import main

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if "--mcp" not in sys.argv:
            raise
        print(json.dumps({"success": False, "error": str(error)}, ensure_ascii=False))
        sys.exit(1)
