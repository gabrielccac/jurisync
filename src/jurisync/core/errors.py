"""One error model for every tool: a stable code, the next step, whether repeating helps and who resolves it.

Connectors raise these classes (or the login errors of ``session_pool``, which map onto the same model) and
``core.mcp.tool_errors`` renders any anticipated failure in one format, which reaches the agent as ``isError``::

    [otp_refused] PJe: the portal refused the authenticator code
    Próximo passo: não repita agora; o código do autenticador foi recusado: confira a conta escolhida no QR ...
    retryable=false · quem resolve: usuário · ref=c-7f3a9e

Anything ``describe`` does not recognize is a bug: its text stays in the server log and the agent gets the generic
``[unexpected]`` error with the same ref.
"""

import urllib.error
from dataclasses import dataclass

from jurisync.core import calls, credentials
from jurisync.core.session_pool import EXPLANATIONS, LoginFailed, SessionBusy, SessionExpired

AGENT, USER, OPERATOR = "agent", "user", "operator"
# Whoever configures the office's logins and grants: the user locally (.env, jurisync-setup), support when hosted.
SETUP = "setup"
ACTORS = {AGENT: "agente", USER: "usuário", OPERATOR: "suporte"}


def hosted():
    """True inside a hosted office's call, where the operator, not the user, holds the configuration."""
    return bool(credentials.tenant())


def resolve_actor(actor):
    if actor == SETUP:
        return OPERATOR if hosted() else USER
    return actor


def setup_step(setting=""):
    """The next step for something only the office's configuration can fix."""
    if hosted():
        return "não repita; peça ao usuário para acionar o suporte do Jurisync, que configura o escritório no servidor."
    what = setting or "o que falta"
    return f"não repita; peça ao usuário para configurar {what} no .env do escritório ou com jurisync-setup."


def with_setting(message, setting):
    """The message naming the setting that fixes it, in both modes: the operator sets the same name in the vault."""
    return f"{message} ({setting})" if setting and setting not in message else message


class JurisyncError(Exception):
    """An anticipated failure. Subclasses set the defaults; any of them can be overridden per raise."""

    code = "request_failed"
    retryable = False
    actor = AGENT
    next_step = "leia a mensagem; não repita a mesma chamada sem mudar algo."

    def __init__(self, message="", *, code=None, next_step=None, retryable=None, actor=None):
        super().__init__(message)
        for name, value in (("code", code), ("next_step", next_step), ("retryable", retryable), ("actor", actor)):
            if value is not None:
                setattr(self, name, value)


class InputError(JurisyncError, ValueError):
    """The arguments cannot work: the agent fixes them and calls again."""

    code = "invalid_input"
    next_step = "corrija os argumentos conforme a mensagem e chame de novo."


class NotFound(JurisyncError, LookupError):
    code = "not_found"
    next_step = "confira o número ou o id; use a tool de listagem do mesmo sistema para achar o certo."


class PortalError(JurisyncError, RuntimeError):
    """The portal answered, but refused the request or with something the connector cannot read."""

    code = "portal_error"
    next_step = "não repita a mesma chamada; relate a mensagem ao usuário."


class NotSupported(JurisyncError, NotImplementedError):
    """A portal or operation the connector does not cover yet."""

    code = "not_supported"
    next_step = "não repita; este portal ou operação ainda não é atendido pelo conector. Avise o usuário."


class PortalUnavailable(JurisyncError, RuntimeError):
    code = "portal_unavailable"
    retryable = True
    next_step = "tente de novo em alguns minutos; se continuar, avise o usuário que o portal está fora do ar."


class NotConfigured(JurisyncError, RuntimeError):
    """The office lacks a setting (login, certificate, key). ``setting`` names it for the local next step."""

    code = "not_configured"
    actor = SETUP

    def __init__(self, message="", *, setting="", **options):
        options.setdefault("next_step", setup_step(setting))
        super().__init__(with_setting(message, setting), **options)


class WritesDisabled(JurisyncError, PermissionError):
    """A write the office has not enabled. ``setting`` is the grant variable."""

    code = "writes_disabled"
    actor = SETUP

    def __init__(self, message="", *, setting="", **options):
        if "next_step" not in options:
            options["next_step"] = "não repita; a escrita não está liberada para este escritório. " + setup_step(
                setting
            ).removeprefix("não repita; ")
        super().__init__(with_setting(message, setting), **options)


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    next_step: str
    retryable: bool
    actor: str


# Login reasons: who acts and whether a new attempt can pass. The next step is the operator's EXPLANATIONS line.
LOGIN = {
    "credentials_refused": (SETUP, False),
    "password_expired": (USER, False),
    "otp_refused": (SETUP, False),
    "certificate_refused": (SETUP, False),
    "not_registered": (USER, False),
    "blocked": (USER, False),
    "captcha": (AGENT, True),
    "portal_unavailable": (AGENT, True),
    "login_missing": (SETUP, False),
    "unexpected": (OPERATOR, False),
}

NETWORK = (TimeoutError, ConnectionError, urllib.error.URLError)


def _network_errors():
    """Network failures of the HTTP and browser libraries, looked up only once loaded (nothing raised them before)."""
    import sys

    found = list(NETWORK)
    for module, name in (
        ("curl_cffi.requests.exceptions", "RequestException"),
        ("selenium.common.exceptions", "WebDriverException"),
    ):
        loaded = sys.modules.get(module)
        if loaded is not None:
            found.append(getattr(loaded, name))
    return tuple(found)


def describe(error):
    """The ``Problem`` of an anticipated failure, or None for a bug (which must not reach the agent as text)."""
    text = str(error)
    if isinstance(error, JurisyncError):
        return Problem(error.code, text, error.next_step, error.retryable, resolve_actor(error.actor))
    if isinstance(error, LoginFailed):
        reason = error.reason if error.reason in LOGIN else "unexpected"
        actor, retryable = LOGIN[reason]
        step = EXPLANATIONS[reason]
        step = ("tente de novo em alguns minutos; " if retryable else "não repita agora; ") + step + "."
        if actor == SETUP and hosted():
            step = step.replace("confira", "peça ao suporte para conferir", 1)
        return Problem(reason, text, step, retryable, resolve_actor(actor))
    if isinstance(error, SessionBusy):
        return Problem(
            "busy", text, "outra chamada do escritório usa este portal; tente de novo em um minuto.", True, AGENT
        )
    if isinstance(error, (SessionExpired, *_network_errors())):
        return Problem("portal_unavailable", text, PortalUnavailable.next_step, True, AGENT)
    if isinstance(error, PermissionError):
        return Problem("refused", text, "não repita; esta operação não é permitida por este conector.", False, AGENT)
    if isinstance(error, OSError):
        return Problem("local_error", text, setup_step(), False, resolve_actor(SETUP))
    if isinstance(error, ValueError):
        return Problem(
            "invalid_request", text, "confira os argumentos e a mensagem; só repita mudando algo.", False, AGENT
        )
    if isinstance(error, RuntimeError) and not isinstance(error, (NotImplementedError, RecursionError)):
        return Problem(PortalError.code, text, PortalError.next_step, False, AGENT)
    return None


def render(label, problem, clean):
    """The agent's text: code and message, the next step, then retry, actor and the call's ref."""
    message = clean(problem.message) or "sem detalhes"
    meta = f"retryable={'true' if problem.retryable else 'false'} · quem resolve: {ACTORS.get(problem.actor, problem.actor)}"
    if calls.current():
        meta += f" · ref={calls.current()}"
    return f"[{problem.code}] {label}: {message}\nPróximo passo: {problem.next_step}\n{meta}"


UNEXPECTED = Problem(
    "unexpected",
    "falha inesperada no servidor",
    "não repita; informe ao usuário o ref abaixo para o suporte localizar o erro no log.",
    False,
    OPERATOR,
)
