from jurisync.connectors.gdrive.filing_folder import main
from jurisync.core.mcp import load_env_file

if __name__ == "__main__":
    load_env_file()
    main()
