"""One instance per tenant behind a single module-level name.

A connector that keeps an account's state in an object (ADVBOX: cookie jar, CSRF token, rate-limit cooldown) creates it
once at import. On the hosted server that one object would serve every office, so the server module holds a
``TenantLocal`` instead: attribute access reaches the current tenant's instance, created on first use. A local install
has the single tenant "" and behaves as before. An instance made under other credentials (``credentials.signature()``)
is closed and replaced.
"""

import threading

from jurisync.core import credentials


class TenantLocal:
    def __init__(self, factory, close=None):
        self._factory = factory
        self._close = close
        self._instances = {}  # tenant -> (instance, credentials signature)
        self._lock = threading.Lock()

    def get(self):
        tenant, signature = credentials.tenant(), credentials.signature()
        with self._lock:
            held = self._instances.get(tenant)
            if held is not None and held[1] == signature:
                return held[0]
            instance = self._factory()
            self._instances[tenant] = (instance, signature)
        if held is not None and self._close is not None:
            self._close(held[0])
        return instance

    def __getattr__(self, name):
        return getattr(self.get(), name)

    def forget(self):
        """Drop the current tenant's instance, e.g. after its credentials changed."""
        with self._lock:
            held = self._instances.pop(credentials.tenant(), None)
        if held is not None and self._close is not None:
            self._close(held[0])

    def close(self):
        with self._lock:
            instances, self._instances = [instance for instance, _ in self._instances.values()], {}
        for instance in instances:
            if self._close is not None:
                self._close(instance)
