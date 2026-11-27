"""``python -m jurisync.connectors.pje.auth.a3 pin-set|check``, the command the installation guides give, and the names scripts
imported from here; the A3 token code is ``jurisync.core.a3``, shared with the Eproc certificate login."""

from jurisync.core.a3 import (  # noqa: F401 - the public names of the old module, for scripts that import them here
    A3Signer,
    PinError,
    certificate_chain,
    held_token,
    lock_path,
    main,
    md5_digest_info,
    module_path,
    stored_pin,
    token_lock,
)

if __name__ == "__main__":
    main()
