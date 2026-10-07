"""Diagnose INSS GERID login and the PAT notifications endpoint.

    python cli/inss_probe.py login [--headless] [--keep-open]
    python cli/inss_probe.py notifications [--observe]

`notifications` calls the API directly like the MCP server; `--observe` instead
waits for the portal UI's own request, useful when the direct call breaks.
Output shows response shape only; --show-secrets adds tokens and payloads.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from jurisync.connectors.inss.auth import default_profile, login_and_authorize, qr_secret
from jurisync.connectors.inss.client import TASKS_URL, InssClient
from jurisync.core.mcp import load_env_file

NOTIFICATIONS_PATH = "/apis/requerimentosPortalApi/requerimento/ec/notificacao?first=0"


def _shape(payload, include):
    shape = {
        "topLevelKeys": sorted(payload) if isinstance(payload, dict) else [],
        "notificationCount": len(payload)
        if isinstance(payload, list)
        else len(payload["notificacoes"])
        if isinstance(payload, dict) and isinstance(payload.get("notificacoes"), list)
        else None,
    }
    return {**shape, "payload": payload} if include else shape


async def run(args):
    secret = os.environ.get("INSS_GERID_TOTP_SECRET")
    if not secret and args.qr_image:
        secret = qr_secret(args.qr_image, args.account)
    timeout_ms = args.timeout * 1000
    browser = await login_and_authorize(
        profile=args.profile or os.environ.get("INSS_BROWSER_PROFILE") or default_profile(),
        profile_directory=args.profile_directory or os.environ.get("INSS_BROWSER_PROFILE_DIRECTORY"),
        secret=secret,
        otp=os.environ.get("INSS_GERID_OTP"),
        timeout_ms=timeout_ms,
        headless=args.headless,
    )
    try:
        output = dict(browser.summary)
        if args.show_secrets:
            output["storageCredentials"] = browser.credentials
        page = browser.page
        if args.command == "notifications":
            if args.observe:
                async with page.expect_response(
                    lambda response: NOTIFICATIONS_PATH in response.url, timeout=timeout_ms
                ) as info:
                    await page.goto(TASKS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                response = await info.value
                try:
                    body = _shape(await response.json(), args.show_secrets)
                except Exception:
                    body = {"json": False}
                output["response"] = {
                    "mode": "observed",
                    "method": response.request.method,
                    "status": response.status,
                    **body,
                }
            else:
                await page.goto(TASKS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                token = await InssClient._recaptcha(page)
                payload = await InssClient._request(page, NOTIFICATIONS_PATH, "POST", {"tokenReCAPTCHA": token})
                output["response"] = {"mode": "direct", **_shape(payload, args.show_secrets)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.keep_open:
            await asyncio.to_thread(input, "Browser left open (--keep-open). Press Enter to close it.")
    finally:
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("login", "notifications"))
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--profile-directory")
    parser.add_argument("--qr-image", type=Path)
    parser.add_argument("--account", default="1")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--show-secrets", action="store_true")
    parser.add_argument("--timeout", type=int, default=120, help="seconds")
    args = parser.parse_args()
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
