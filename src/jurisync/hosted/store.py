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
    created integer not null
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
create table if not exists web_sessions (
    digest text primary key,
    email text not null,
    tenant text not null,
    csrf text not null,
    expires integer not null
);
create table if not exists audit (
    id integer primary key autoincrement,
    at integer not null,
    tenant text not null,
    actor text not null,
    action text not null,
    detail text not null default ''
);
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


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma foreign_keys = on")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.path.chmod(0o600)

    def _run(self, sql, *args):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # Tenants and users

    def add_tenant(self, tenant, name, *, customer="", connectors=("pje",)):
        require_tenant_id(tenant)
        self._run(
            "insert into tenants (id, name, customer, connectors, created) values (?, ?, ?, ?, ?)",
            tenant,
            name,
            customer,
            ",".join(connectors),
            int(time.time()),
        )

    def tenant(self, tenant):
        rows = self._run("select * from tenants where id = ?", tenant)
        if not rows:
            return None
        row = dict(rows[0])
        row["connectors"] = [name for name in row["connectors"].split(",") if name]
        return row

    def tenants(self):
        return [self.tenant(row["id"]) for row in self._run("select id from tenants order by id")]

    def remove_tenant(self, tenant):
        self._run("delete from web_sessions where tenant = ?", tenant)
        self._run("delete from portal_checks where tenant = ?", tenant)
        self._run("delete from tokens where tenant = ?", tenant)
        self._run("delete from codes where tenant = ?", tenant)
        self._run("delete from tenants where id = ?", tenant)

    def add_user(self, email, tenant, password):
        if self.tenant(tenant) is None:
            raise ValueError(f"Unknown tenant {tenant}")
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
        self._run("delete from web_sessions where email = ?", email)

    def remove_user(self, email):
        email = email.strip().lower()
        self._run("delete from web_sessions where email = ?", email)
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

    # Office web sessions, audit trail and portal checks

    def open_web_session(self, email, tenant, ttl):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self._run(
            "insert into web_sessions (digest, email, tenant, csrf, expires) values (?, ?, ?, ?, ?)",
            digest(token),
            email,
            tenant,
            csrf,
            int(time.time()) + ttl,
        )
        return token

    def web_session(self, token):
        rows = self._run("select email, tenant, csrf, expires from web_sessions where digest = ?", digest(token or ""))
        if not rows or rows[0]["expires"] < time.time():
            return None
        return dict(rows[0])

    def close_web_session(self, token):
        self._run("delete from web_sessions where digest = ?", digest(token or ""))

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
        self._run("delete from web_sessions where expires < ?", int(now))
        self._run("delete from codes where expires < ?", now)
        self._run("delete from tokens where expires < ?", int(now))
