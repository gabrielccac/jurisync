"""``python -m jurisync.connectors.pje.auth.a3 pin-set|check``, the command the installation guides give; the A3 token
code is ``jurisync.core.a3``, shared with the Eproc certificate login."""

from jurisync.core.a3 import main

if __name__ == "__main__":
    main()
