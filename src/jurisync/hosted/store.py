"""Tenants, their users, and OAuth state, in one SQLite file.

A tenant is one office (one set of portal credentials in the vault). A user signs in with an e-mail and password and
belongs to exactly one tenant; every token issued to that user acts for that tenant only. Tokens and authorization codes
are stored as SHA-256 hashes, so a copy of this file does not let anyone call the server.
"""

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from jurisync.hosted.vault import require_tenant_id

SCHEMA = """
create table if not exists tenants (
    id text primary key,
    name text not null,
    customer text not null default '',
    connectors text not null default 'pje',
    created integer not null,
    mode text not null default 'hosted',
    reason text not null default ''
);
create table if not exists users (
    email text primary key,
    tenant text not null references tenants(id) on delete cascade,
    password text not null,
    created integer not null
);
create table if not exists clients (
    client_id text primary key,
    info text not null,
    created integer not null
);
create table if not exists codes (
    digest text primary key,
    tenant text not null,
    record text not null,
    expires real not null
);
drop table if exists web_sessions;
create table if not exists audit (
    id integer primary key autoincrement,
    at integer not null,
    tenant text not null,
    actor text not null,
    action text not null,
    detail text not null default ''
);
create table if not exists calls (
    id integer primary key autoincrement,
    at integer not null,
    tenant text not null,
    user text not null,
    connector text not null,
    tool text not null,
    ok integer not null,
    error text not null default '',
    duration_ms integer not null,
    portal text not null default '',
    reason text not null default ''
);
create index if not exists calls_by_tenant on calls (tenant, id);
create index if not exists calls_by_time on calls (at);
create table if not exists logins (
    id integer primary key autoincrement,
    at integer not null,
    tenant text not null,
    portal text not null,
    reason text not null,
    duration_ms integer not null
);
create index if not exists logins_by_time on logins (at);
create table if not exists portal_checks (
    tenant text not null,
    portal text not null,
    ok integer not null,
    message text not null,
    at integer not null,
    primary key (tenant, portal)
);
create table if not exists tokens (
    digest text primary key,
    kind text not null,
    client_id text not null,
    tenant text not null,
    subject text not null,
    scopes text not null,
    resource text,
    expires integer not null,
    pair text not null
);
"""

# Tool calls and logins are kept this long, then dropped by ``purge``.
CALLS_KEPT = 90 * 24 * 3600
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def digest(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def hash_password(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${derived.hex()}"


def check_password(password, stored):
    try:
        scheme, salt, expected = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), **SCRYPT)
    return hmac.compare_digest(derived.hex(), expected)


def client_name(info):
    """The name an OAuth client registered with ("Claude", "ChatGPT"); empty when it gave none."""
    try:
        return str(json.loads(info or "{}").get("client_name") or "")
    except ValueError:
        return ""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # serve, jurisync-admin and backup.sh open this file from separate processes: readers never block the writer,
        # and a writer waits for another instead of failing with "database is locked".
        self._db.execute("pragma busy_timeout = 5000")
        self._db.execute("pragma journal_mode = wal")
        self._db.execute("pragma foreign_keys = on")
        self._db.executescript(SCHEMA)
        for table, column, definition in (
            ("tenants", "mode", "text not null default 'hosted'"),  # stores created before local-only offices existed
            ("tenants", "reason", "text not null default ''"),
            ("calls", "portal", "text not null default ''"),  # and before the monitoring API
            ("calls", "reason", "text not null default ''"),
        ):
            if column not in {row["name"] for row in self._db.execute(f"pragma table_info({table})")}:
                self._db.execute(f"alter table {table} add column {column} {definition}")
        self._lock = threading.Lock()
        self.path.chmod(0o600)

    def _run(self, sql, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # Tenants and users

    def add_tenant(self, tenant, name, *, customer="", connectors=("pje",), mode="hosted", reason=""):
        """``mode="local"``: an office that stays on its own install; it is remembered here with no connectors."""
        require_tenant_id(tenant)
        if mode not in ("hosted", "local"):
            raise ValueError("mode must be hosted or local")
        self._run(
            "insert into tenants (id, name, customer, connectors, created, mode, reason) values (?, ?, ?, ?, ?, ?, ?)",
            tenant,
            name,
            customer,
            "" if mode == "local" else ",".join(connectors),
            int(time.time()),
            mode,
            reason,
        )

    def tenant(self, tenant):
        rows = self._run("select * from tenants where id = ?", tenant)
        if not rows:
            return None
        row = dict(rows[0])
        row["connectors"] = [name for name in row["connectors"].split(",") if name]
        return row

    def update_tenant(self, tenant, *, name=None, connectors=None, mode=None, reason=None):
        """Change an office's display name, connectors or mode; its users, tokens and vault stay.

        A local office never has connectors: setting ``mode="local"`` clears them.
        """
        if name is not None:
            self._run("update tenants set name = ? where id = ?", name, tenant)
        if mode is not None:
            if mode not in ("hosted", "local"):
                raise ValueError("mode must be hosted or local")
            self._run("update tenants set mode = ? where id = ?", mode, tenant)
        if reason is not None:
            self._run("update tenants set reason = ? where id = ?", reason, tenant)
        if connectors is not None:
            self._run("update tenants set connectors = ? where id = ?", ",".join(connectors), tenant)
        current = self.tenant(tenant)
        if current and current["mode"] == "local" and current["connectors"]:
            self._run("update tenants set connectors = '' where id = ?", tenant)

    def tenants(self):
        return [self.tenant(row["id"]) for row in self._run("select id from tenants order by id")]

    def remove_tenant(self, tenant):
        self._run("delete from portal_checks where tenant = ?", tenant)
        self._run("delete from calls where tenant = ?", tenant)
        self._run("delete from logins where tenant = ?", tenant)
        self._run("delete from tokens where tenant = ?", tenant)
        self._run("delete from codes where tenant = ?", tenant)
        self._run("delete from tenants where id = ?", tenant)

    def add_user(self, email, tenant, password):
        office = self.tenant(tenant)
        if office is None:
            raise ValueError(f"Unknown tenant {tenant}")
        if office["mode"] == "local":
            raise ValueError(f"{tenant} uses its local install: it has no users on the server")
        if len(password) < 12:
            raise ValueError("Use a password of at least 12 characters")
        self._run(
            "insert into users (email, tenant, password, created) values (?, ?, ?, ?) "
            "on conflict(email) do update set tenant = excluded.tenant, password = excluded.password",
            email.strip().lower(),
            tenant,
            hash_password(password),
            int(time.time()),
        )

    def authenticate(self, email, password):
        """The user's tenant id when the password matches, else None."""
        rows = self._run("select tenant, password from users where email = ?", email.strip().lower())
        if not rows:
            check_password(password, hash_password("timing-equalizer"))
            return None
        return rows[0]["tenant"] if check_password(password, rows[0]["password"]) else None

    def users(self, tenant=None):
        sql, args = "select email, tenant, created from users", ()
        if tenant:
            sql, args = sql + " where tenant = ?", (tenant,)
        return [dict(row) for row in self._run(sql + " order by email", *args)]

    def set_password(self, email, password):
        if len(password) < 12:
            raise ValueError("Use a password of at least 12 characters")
        email = email.strip().lower()
        self._run("update users set password = ? where email = ?", hash_password(password), email)
        self._run("delete from tokens where subject = ?", email)

    def remove_user(self, email):
        email = email.strip().lower()
        self._run("delete from tokens where subject = ?", email)
        self._run("delete from users where email = ?", email)

    # OAuth clients, codes and tokens

    def save_client(self, client_id, info_json):
        self._run(
            "insert into clients (client_id, info, created) values (?, ?, ?) "
            "on conflict(client_id) do update set info = excluded.info",
            client_id,
            info_json,
            int(time.time()),
        )

    def client(self, client_id):
        rows = self._run("select info from clients where client_id = ?", client_id)
        return rows[0]["info"] if rows else None

    def prune_clients(self, cap):
        """Past ``cap`` registered clients, forget the oldest ones that hold no live token. Returns how many went."""
        excess = self._run("select count(*) as total from clients")[0]["total"] - cap
        if excess <= 0:
            return 0
        idle = self._run(
            "select client_id from clients where not exists ("
            "select 1 from tokens where tokens.client_id = clients.client_id and tokens.expires >= ?"
            ") order by created, rowid limit ?",
            int(time.time()),
            excess,
        )
        for row in idle:
            self._run("delete from clients where client_id = ?", row["client_id"])
        return len(idle)

    def save_code(self, code, tenant, record, expires):
        self._run(
            "insert into codes (digest, tenant, record, expires) values (?, ?, ?, ?)",
            digest(code),
            tenant,
            json.dumps(record),
            expires,
        )

    def code(self, code):
        rows = self._run("select tenant, record, expires from codes where digest = ?", digest(code))
        if not rows or rows[0]["expires"] < time.time():
            return None
        return rows[0]["tenant"], json.loads(rows[0]["record"])

    def delete_code(self, code):
        self._run("delete from codes where digest = ?", digest(code))

    def save_token(self, token, *, kind, client_id, tenant, subject, scopes, resource, expires, pair):
        self._run(
            "insert into tokens (digest, kind, client_id, tenant, subject, scopes, resource, expires, pair) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            digest(token),
            kind,
            client_id,
            tenant,
            subject,
            " ".join(scopes),
            resource,
            expires,
            pair,
        )

    def token(self, token, kind):
        rows = self._run("select * from tokens where digest = ? and kind = ?", digest(token), kind)
        if not rows or rows[0]["expires"] < time.time():
            return None
        row = dict(rows[0])
        row["scopes"] = [scope for scope in row["scopes"].split(" ") if scope]
        return row

    def delete_pair(self, token):
        """Revoke a token and the access/refresh token issued with it."""
        rows = self._run("select pair from tokens where digest = ?", digest(token))
        if rows:
            self._run("delete from tokens where pair = ?", rows[0]["pair"])

    # Audit trail and portal checks

    def log(self, tenant, actor, action, detail=""):
        self._run(
            "insert into audit (at, tenant, actor, action, detail) values (?, ?, ?, ?, ?)",
            int(time.time()),
            tenant,
            actor,
            action,
            detail,
        )

    def history(self, tenant, limit=30):
        rows = self._run("select * from audit where tenant = ? order by id desc limit ?", tenant, limit)
        return [dict(row) for row in rows]

    def log_call(self, tenant, user, connector, tool, error, duration_ms, portal="", reason=""):
        """One line per tool call: who, which tool and portal, how it ended (``reason``: why a login failed).

        Arguments and results are never stored; ``portal`` is only ever a portal key.
        """
        self._run(
            "insert into calls (at, tenant, user, connector, tool, ok, error, duration_ms, portal, reason) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            int(time.time()),
            tenant,
            user,
            connector,
            tool,
            int(not error),
            error,
            duration_ms,
            portal,
            reason,
        )

    def calls(self, tenant, limit=30):
        rows = self._run("select * from calls where tenant = ? order by id desc limit ?", tenant, limit)
        return [dict(row) for row in rows]

    def recent_calls(self, *, tenant="", connector="", portal="", errors=False, limit=100):
        """The latest calls of every office, narrowed by the filters given."""
        where, args = [], []
        for column, value in (("tenant", tenant), ("connector", connector), ("portal", portal)):
            if value:
                where.append(f"{column} = ?")
                args.append(value)
        if errors:
            where.append("ok = 0")
        clause = f" where {' and '.join(where)}" if where else ""
        rows = self._run(f"select * from calls{clause} order by id desc limit ?", *args, limit)
        return [dict(row) for row in rows]

    def calls_since(self, at):
        return [dict(row) for row in self._run("select * from calls where at >= ?", int(at))]

    def last_portal_calls(self):
        """The latest call of each office on each portal."""
        rows = self._run(
            "select * from calls where id in (select max(id) from calls where portal != '' group by tenant, portal)"
        )
        return [dict(row) for row in rows]

    def log_login(self, tenant, portal, reason, duration_ms):
        """One line per portal login tried; ``reason`` is empty when it got in."""
        self._run(
            "insert into logins (at, tenant, portal, reason, duration_ms) values (?, ?, ?, ?, ?)",
            int(time.time()),
            tenant,
            portal,
            reason,
            duration_ms,
        )

    def logins_since(self, at):
        return [dict(row) for row in self._run("select * from logins where at >= ?", int(at))]

    def signed_in(self):
        """Users with a live sign-in, per client (Claude, ChatGPT) and address; never the tokens."""
        rows = self._run(
            "select tenant, subject, tokens.client_id, resource, clients.info, max(expires) as until, "
            "(select max(at) from calls where calls.tenant = tokens.tenant and user = subject) as last_call "
            "from tokens left join clients on clients.client_id = tokens.client_id where expires >= ? "
            "group by tenant, subject, tokens.client_id, resource order by tenant, subject",
            int(time.time()),
        )
        return [
            {
                "escritorio": row["tenant"],
                "usuario": row["subject"],
                "cliente": client_name(row["info"]),
                "conector": row["resource"] or "",
                "valido_ate": row["until"],
                "ultima_chamada": row["last_call"],
            }
            for row in rows
        ]

    def save_check(self, tenant, portal, ok, message):
        self._run(
            "insert into portal_checks (tenant, portal, ok, message, at) values (?, ?, ?, ?, ?) "
            "on conflict(tenant, portal) do update set ok = excluded.ok, message = excluded.message, at = excluded.at",
            tenant,
            portal,
            int(bool(ok)),
            message,
            int(time.time()),
        )

    def checks(self, tenant):
        rows = self._run("select portal, ok, message, at from portal_checks where tenant = ?", tenant)
        return {row["portal"]: dict(row) for row in rows}

    def forget_check(self, tenant, portal):
        self._run("delete from portal_checks where tenant = ? and portal = ?", tenant, portal)

    def purge(self):
        now = time.time()
        self._run("delete from codes where expires < ?", now)
        self._run("delete from tokens where expires < ?", int(now))
        self._run("delete from calls where at < ?", int(now) - CALLS_KEPT)
        self._run("delete from logins where at < ?", int(now) - CALLS_KEPT)
