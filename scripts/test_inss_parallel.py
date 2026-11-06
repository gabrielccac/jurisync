#!/usr/bin/env python3
"""Run get_inss_payments for several clients at once against the local inss-mcp server.

Credentials never leave this machine: they are read from a JSON file you fill in
(default downloaded_files/inss_clients.json, which is gitignored) and are not printed.

    /home/gabriel/Documents/captcha-solver/.venv/bin/python3 scripts/test_inss_parallel.py \
        --clients downloaded_files/inss_clients.json --mode isolated

clients file: [{"label": "cliente1", "cpf": "00000000000", "password": "..."}, ...]
A missing or empty "password" makes the server fall back to GOVBR_PASSWORD from its .env.

--mode isolated: one inss-mcp process per client (no shared state, the safe baseline).
--mode shared:   one inss-mcp process, all calls in flight together (tests its concurrency).
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = "/home/gabriel/Documents/captcha-solver/.venv/bin/inss-mcp"


def mask(cpf):
    digits = "".join(c for c in cpf if c.isdigit())
    return f"***.{digits[3:6]}.{digits[6:9]}-**" if len(digits) == 11 else "***"


def parse(result):
    text = "".join(getattr(part, "text", "") for part in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


async def call(session, client, started):
    args = {"cpf": client["cpf"]}
    if client.get("password"):
        args["password"] = client["password"]
    begin = time.monotonic()
    row = {"label": client["label"], "cpf": mask(client["cpf"]), "start": round(begin - started, 2)}
    try:
        result = await session.call_tool("get_inss_payments", args)
        row["ok"] = not result.isError
        row["data"] = parse(result)
    except Exception as error:
        row["ok"] = False
        row["data"] = f"{type(error).__name__}: {error}"
    row["seconds"] = round(time.monotonic() - begin, 2)
    return row


async def run_isolated(clients):
    started = time.monotonic()

    async def one(client):
        async with stdio_client(StdioServerParameters(command=SERVER)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await call(session, client, started)

    return await asyncio.gather(*(one(client) for client in clients))


async def run_shared(clients):
    async with stdio_client(StdioServerParameters(command=SERVER)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [tool.name for tool in (await session.list_tools()).tools]
            print("tools:", tools)
            started = time.monotonic()
            return await asyncio.gather(*(call(session, client, started) for client in clients))


def summarise(row):
    data = row["data"]
    keys = ", ".join(data) if isinstance(data, dict) else str(data)[:120]
    return f"{row['label']:<12} {row['cpf']:<16} {'OK ' if row['ok'] else 'ERR'} {row['seconds']:>7}s  {keys}"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", default="downloaded_files/inss_clients.json")
    parser.add_argument("--mode", choices=["isolated", "shared"], default="isolated")
    parser.add_argument("--out", default="downloaded_files/inss_parallel_result.json")
    options = parser.parse_args()
    clients = json.loads(Path(options.clients).read_text())
    wall = time.monotonic()
    rows = await (run_isolated if options.mode == "isolated" else run_shared)(clients)
    wall = round(time.monotonic() - wall, 2)
    for row in rows:
        print(summarise(row))
    slowest = max(row["seconds"] for row in rows)
    total = round(sum(row["seconds"] for row in rows), 2)
    print(f"\nmode={options.mode} clients={len(rows)} wall={wall}s slowest={slowest}s sum={total}s")
    print("parallel" if wall < total * 0.8 else "looks serialised (wall ~ sum of calls)")
    Path(options.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print("full results:", options.out)


asyncio.run(main())
