from jurisync.core.mcp import load_env_file
from jurisync.paths import REPO_ROOT
from jurisync.workflows.filing.prepare import main

if __name__ == "__main__":
    load_env_file(REPO_ROOT / ".env")
    main()
