"""Authenticated portal sessions kept in memory by a long-running MCP server.

Each key (portal) holds at most one session per tenant: a hosted server keeps each office's
login apart (``credentials.tenant()``), a local install has the single tenant "". Calls for the
same tenant and key are serialized because JSF/PHP view state is not safe to share concurrently.
A session idle longer than ``revalidate_after`` seconds is checked with
``refresh`` before reuse; a failed check or a failed call triggers a new login.

Logins are the expensive, risky step: at most ``JURISYNC_MAX_LOGINS`` run at once in the process, and
credentials a portal rejected are not sent again for ``login_cooldown`` seconds (a few wrong passwords lock
the account). Sessions unused for ``idle_ttl`` seconds are closed the next time the pool is used.

A session and a cool-down hold the ``credentials.signature()`` they were made under: once the office's credentials
change (the operator's ``jurisync-hosted`` runs in another process), the session is closed and the cool-down lifted, so
the next call logs in with the new credentials.
"""

import os
import threading
import time
from contextlib import contextmanager

from jurisync.core import credentials

DEFAULT_LOGIN_COOLDOWN = 15 * 60
DEFAULT_IDLE_TTL = 30 * 60
DEFAULT_MAX_LOGINS = 2


class SessionExpired(RuntimeError):
    """Raised by a connector when the portal sent the session back to its login page.

    ``SessionPool.run`` answers it with one fresh login and a repeat of the call.
    """


# Why a login failed, the same words for every system. The first six mean the portal refused what was sent.
REFUSALS = (
    "credentials_refused",
    "password_expired",
    "otp_refused",
    "certificate_refused",
    "not_registered",
    "blocked",
)
REASONS = (*REFUSALS, "captcha", "portal_unavailable", "login_missing", "unexpected")


class LoginFailed(RuntimeError):
    """A login that did not go through; ``reason`` is one of ``REASONS``."""

    reason = "unexpected"

    def __init__(self, message="", *, reason=None):
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class MissingLogin(LoginFailed):
    """The account sets no login for this system (user, password, certificate or authenticator); nothing was sent."""

    reason = "login_missing"


class LoginRejected(LoginFailed):
    """Raised by a connector's login when the portal refused the credentials (wrong password, OTP or certificate).

    Network and page errors are not this. The pool never retries it and blocks further logins for the same
    tenant and portal until the cool-down passes, the credentials signature changes or ``forget`` is called.
    ``reason`` is one of ``REFUSALS``, or ``unexpected`` when the portal stopped the login for something none of them
    names (a profile to choose, an account action the connector never takes).
    """

    reason = "credentials_refused"


# The operator's one line per reason (setup and hosted checks).
EXPLANATIONS = {
    "credentials_refused": "o portal recusou o usuário ou a senha: confira o login configurado",
    "password_expired": "senha expirada: o advogado precisa trocar no portal",
    "otp_refused": "o código do autenticador foi recusado: confira a conta escolhida no QR (OTP_PROFILE_MATCH/INDEX)",
    "certificate_refused": "o portal recusou o certificado A1: confira o arquivo, o PIN e a validade",
    "not_registered": "o tribunal não tem cadastro para este certificado/CPF",
    "blocked": "a conta está bloqueada ou desativada no portal: o advogado precisa falar com o tribunal",
    "captcha": "o portal pediu um CAPTCHA que não foi resolvido",
    "portal_unavailable": "o portal está fora do ar ou não respondeu: tente mais tarde",
    "login_missing": "falta configurar o login (usuário, senha, certificado ou autenticador)",
    "unexpected": "falha inesperada no login: veja a mensagem",
}


def login_reason(error):
    """The ``REASONS`` code of a failed login: the one the connector set, else what the exception type tells."""
    reason = getattr(error, "reason", None)
    if reason in REASONS:
        return reason
    from curl_cffi.requests import exceptions

    if isinstance(error, (exceptions.ConnectionError, exceptions.Timeout, ConnectionError, TimeoutError)):
        return "portal_unavailable"
    return "unexpected"


def _env_number(name, default, convert=float):
    try:
        return convert(os.environ.get(name) or default)
    except ValueError:
        return default


# ``on_login(portal, error, duration_ms)`` after every login a pool tries (hosted telemetry); error is None on success.
on_login = None


def _report_login(portal, error, began):
    if on_login is None:
        return
    try:
        on_login(portal, error, round((time.monotonic() - began) * 1000))
    except Exception:
        pass  # Telemetry never costs a login.


_login_slots = None
_login_slots_guard = threading.Lock()


def login_slots():
    """The process-wide bound on concurrent logins, shared by every pool."""
    global _login_slots
    with _login_slots_guard:
        if _login_slots is None:
            _login_slots = threading.BoundedSemaphore(
                max(1, _env_number("JURISYNC_MAX_LOGINS", DEFAULT_MAX_LOGINS, int))
            )
        return _login_slots


class SessionPool:
    def __init__(
        self,
        login,
        refresh,
        close,
        *,
        revalidate_after=120,
        login_cooldown=None,
        idle_ttl=DEFAULT_IDLE_TTL,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self._login = login
        self._refresh = refresh
        self._close = close
        self._revalidate_after = revalidate_after
        self._login_cooldown = login_cooldown
        self._idle_ttl = idle_ttl
        self._clock = clock
        self._wall_clock = wall_clock
        self._entries = {}  # key -> (session, last successful use; -inf after a failed call, credentials signature)
        self._used = {}  # key -> last use of any kind, for idle eviction
        self._rejected = {}  # key -> (clock time, wall time, reason, credentials signature) of the last LoginRejected
        self._locks = {}
        self._guard = threading.Lock()
        self._only = None  # the portals a local server offers; None logs in anywhere (hosted: the office decides)

    def _cooldown(self):
        if self._login_cooldown is not None:
            return self._login_cooldown
        return _env_number("JURISYNC_LOGIN_COOLDOWN_SECONDS", DEFAULT_LOGIN_COOLDOWN)

    def _lock(self, key):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    @contextmanager
    def _held(self, key):
        """Hold the key's lock. Eviction may drop an idle lock between lookup and acquire: then take the new one."""
        while True:
            lock = self._lock(key)
            lock.acquire()
            with self._guard:
                current = self._locks.get(key) is lock
            if current:
                break
            lock.release()
        try:
            yield
        finally:
            lock.release()

    def _touch(self, key):
        with self._guard:
            self._used[key] = self._clock()

    def _discard(self, key):
        with self._guard:
            self._used.pop(key, None)
            entry = self._entries.pop(key, None)
        if entry is not None:
            try:
                self._close(entry[0])
            except Exception:
                pass  # Closing a stale session must not block a fresh login.

    def _evict_idle(self, keep=None):
        """Close sessions unused for ``idle_ttl`` and drop their locks. A key whose lock is held is in use: skipped."""
        if self._idle_ttl is None:
            return
        now = self._clock()
        with self._guard:
            idle = [key for key, used in self._used.items() if key != keep and now - used > self._idle_ttl]
            expired = [key for key, (at, *_) in self._rejected.items() if now - at >= self._cooldown()]
            for key in expired:
                del self._rejected[key]
        for key in idle:
            with self._guard:
                lock = self._locks.get(key)
                if lock is None or not lock.acquire(blocking=False):
                    continue
            try:
                with self._guard:
                    still_idle = key in self._used and self._clock() - self._used[key] > self._idle_ttl
                if still_idle:
                    self._discard(key)
                    with self._guard:
                        self._locks.pop(key, None)
            finally:
                lock.release()

    def _check_cooldown(self, key):
        with self._guard:
            rejected = self._rejected.get(key)
            if rejected is not None and rejected[3] != credentials.signature():
                del self._rejected[key]  # the credentials changed since the portal refused them
                return
        if rejected is None:
            return
        remaining = self._cooldown() - (self._clock() - rejected[0])
        if remaining <= 0:
            return
        at = time.strftime("%H:%M", time.localtime(rejected[1]))
        raise LoginRejected(
            f"{key[1]}: the portal rejected these credentials at {at}. Correct them, or wait "
            f"{max(1, round(remaining / 60))} min before trying again; no new login was attempted",
            reason=rejected[2],
        )

    def only(self, portals):
        """Refuse a login outside ``portals``: the account has no login configured there."""
        self._only = tuple(portals)

    def _new_login(self, key, portal, login=None):
        if self._only is not None and portal not in self._only:
            raise ValueError(
                f"No login is configured for {portal} on this account; configured: {', '.join(self._only)}"
            )
        self._check_cooldown(key)
        with login_slots():
            began = time.monotonic()
            try:
                value = (login or self._login)(portal)
            except LoginRejected as error:
                with self._guard:
                    self._rejected[key] = (self._clock(), self._wall_clock(), error.reason, credentials.signature())
                _report_login(portal, error, began)
                raise
            except Exception as error:
                _report_login(portal, error, began)
                raise
            _report_login(portal, None, began)
            return value

    def fresh_login(self, portal, login=None):
        """A new login under the pool's rules (cool-down, login slots) that the pool does not keep; the caller closes it."""
        return self._new_login((credentials.tenant(), portal), portal, login)

    def check(self, portal):
        """Prove the current credentials with a fresh login under the pool's rules; nothing is kept."""
        self._close(self.fresh_login(portal))

    @contextmanager
    def session(self, portal):
        key = (credentials.tenant(), portal)
        signature = credentials.signature()
        self._evict_idle(keep=key)
        with self._held(key):
            self._touch(key)
            entry = self._entries.get(key)
            if entry is not None and entry[2] != signature:
                self._discard(key)
            elif entry is not None and self._clock() - entry[1] > self._revalidate_after:
                try:
                    value = self._refresh(portal, entry[0])
                except Exception:
                    value = None
                if value is None:
                    self._discard(key)
                else:
                    self._entries[key] = (value, self._clock(), signature)
            if key not in self._entries:
                self._entries[key] = (self._new_login(key, portal), self._clock(), signature)
            value = self._entries[key][0]
            try:
                yield value
            except ValueError:
                raise  # Refused input says nothing about the session: keep it as it was.
            except BaseException:
                # The failure may be an expired session: verify before next reuse.
                self._entries[key] = (value, float("-inf"), signature)
                raise
            finally:
                self._touch(key)
            self._entries[key] = (value, self._clock(), signature)

    def run(self, portal, read):
        """``read(session)`` on the pooled session; if it raises ``SessionExpired``, log in again once and repeat.

        Reads must be safe to repeat (every connector read is). A second expiry is reported to the caller, and
        ``LoginRejected`` always is: rejected credentials are never sent twice.
        """
        for attempt in (1, 2):
            try:
                with self.session(portal) as session:
                    return read(session)
            except SessionExpired:
                self._drop(portal)
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _drop(self, portal):
        key = (credentials.tenant(), portal)
        with self._held(key):
            self._discard(key)
        return key

    def forget(self, portal):
        """Drop the current tenant's login for this portal, e.g. after its credentials changed.

        This also ends the cool-down that follows rejected credentials.
        """
        key = self._drop(portal)
        with self._guard:
            self._rejected.pop(key, None)

    def snapshot(self):
        """``(open sessions, cool-downs)`` of every tenant, for the operator: names, times and reasons only."""
        now = self._clock()
        with self._guard:
            entries, used, rejected = dict(self._entries), dict(self._used), dict(self._rejected)
        sessions = [
            {
                "escritorio": tenant,
                "portal": portal,
                "sem_uso_ha_s": round(now - used.get((tenant, portal), now)),
                # After a failed call the next use checks the session first.
                "estado": "a verificar" if last_ok == float("-inf") else "ativa",
            }
            for (tenant, portal), (_value, last_ok, _signature) in entries.items()
        ]
        cooling = [
            {
                "escritorio": tenant,
                "portal": portal,
                "motivo": reason,
                "recusado": int(wall),
                "libera_em_s": round(self._cooldown() - (now - at)),
            }
            for (tenant, portal), (at, wall, reason, _signature) in rejected.items()
            if now - at < self._cooldown()
        ]
        return sessions, cooling

    def close(self):
        with self._guard:
            keys = list(self._entries)
        for key in keys:
            with self._held(key):
                self._discard(key)
