from __future__ import annotations
"""Netlify deploy stage — push the freshly built site/ to Netlify after each run.

Kept SEPARATE from site_builder so the builder stays pure/offline. This module
is the only place in the site path that touches the network. It is fully
OPT-IN and SELF-DISABLING: if the Netlify CLI isn't installed, or the required
env vars aren't set, it logs and returns None instead of raising — so a machine
without deploy credentials (or an offline run) is never affected.

Required environment (put these in .env, which is gitignored — never commit):
    NETLIFY_AUTH_TOKEN   Personal access token (Netlify > User settings >
                         Applications > New access token).
    NETLIFY_SITE_ID      The target site's API ID (Site settings > General >
                         Site information > Site ID). Identifies which site to
                         deploy to in non-interactive mode.

Then each pipeline run publishes site/ to production:
    netlify deploy --prod --dir=site --site=<id>     (token read from env)

Returns the live deploy URL on success, else None.
"""
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("deploy_netlify")


def _cli() -> Optional[str]:
    """Path to the Netlify CLI, or None if it isn't installed."""
    return shutil.which("netlify") or shutil.which("ntl")


def deploy(site_dir: Optional[Path] = None, *, timeout: int = 300) -> Optional[str]:
    """Deploy site_dir to Netlify production. Returns the live URL or None.

    Never raises: any problem (no CLI, no creds, deploy failure) is logged and
    swallowed so the pipeline is never blocked by deployment.
    """
    site_dir = Path(site_dir) if site_dir is not None else (PROJECT_ROOT / "site")

    token = os.environ.get("NETLIFY_AUTH_TOKEN")
    site_id = os.environ.get("NETLIFY_SITE_ID")
    if not token or not site_id:
        log.info("Netlify deploy skipped — NETLIFY_AUTH_TOKEN / NETLIFY_SITE_ID "
                 "not set. (Site built locally; configure .env to auto-publish.)")
        return None

    cli = _cli()
    if not cli:
        log.warning("Netlify deploy skipped — 'netlify' CLI not found on PATH. "
                    "Install with: npm install -g netlify-cli")
        return None

    if not (site_dir / "index.html").exists():
        log.warning("Netlify deploy skipped — no index.html in %s", site_dir)
        return None

    cmd = [cli, "deploy", "--prod", "--dir", str(site_dir),
           "--site", site_id, "--no-build"]
    env = dict(os.environ, NETLIFY_AUTH_TOKEN=token)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, env=env)
    except Exception as e:  # noqa: BLE001
        log.warning("Netlify deploy failed to run: %s", e)
        return None

    if res.returncode != 0:
        # Log stderr but never the token (it's in env, not the command/output).
        log.warning("Netlify deploy returned %s: %s", res.returncode,
                    (res.stderr or res.stdout or "").strip()[:500])
        return None

    url = _extract_url(res.stdout)
    log.info("Netlify deploy succeeded%s", f" — {url}" if url else "")
    return url


def _extract_url(stdout: str) -> Optional[str]:
    """Pull the live production URL out of the CLI output."""
    import re
    # The CLI prints a line like "Website URL: https://<site>.netlify.app".
    m = re.search(r"(Website URL|Unique deploy URL|Live URL)[:\s]+"
                  r"(https?://\S+)", stdout or "")
    if m:
        return m.group(2).strip()
    m = re.search(r"https://[A-Za-z0-9._-]+\.netlify\.app\S*", stdout or "")
    return m.group(0).strip() if m else None


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = deploy(PROJECT_ROOT / "site")
    print(f"Deploy URL: {result}" if result else "Deploy did not run (see log above).")
