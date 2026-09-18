"""Compatibility entry point; implementation lives in integrations.pje.client."""

from integrations.pje.client import fetch_process, main, movement_candidates, publication_candidates

if __name__ == "__main__":
    main()
