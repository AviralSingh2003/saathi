"""Start Saathi locally: seed demo data, run the MCP server and the host, open the browser.

Works with no AWS credentials (MOCK=1, the default). Usage:
    python scripts/demo.py [--keep-data] [--no-browser]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from saathi import config  # noqa: E402
from saathi.seed import seed  # noqa: E402


def wait_for(url: str, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.4)
    raise SystemExit(f"Timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-data", action="store_true", help="don't reset the demo database")
    parser.add_argument("--no-browser", action="store_true", help="don't open browser tabs")
    args = parser.parse_args()

    settings = config.get()
    info = seed(reset=not args.keep_data)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}

    procs = []
    try:
        procs.append(subprocess.Popen([sys.executable, "-m", "saathi.server.app"], cwd=ROOT, env=env))
        wait_for(f"http://{settings.mcp_host}:{settings.mcp_port}/health")
        procs.append(subprocess.Popen([sys.executable, "-m", "saathi.host.main"], cwd=ROOT, env=env))
        wait_for(f"http://{settings.ui_host}:{settings.ui_port}/config.js")

        mode = "MOCK (offline, deterministic router)" if settings.mock or not settings.bedrock_model_id else "LIVE (Bedrock)"
        print("\nSaathi is running —", mode)
        print(f"  MCP server   {settings.mcp_url}")
        print(f"  Elder view   {info['elder_url']}   (Chrome/Edge for voice; press D for the demo clock)")
        print(f"  Family view  {info['family_url']}")
        print(f"               (short link: {info['family_short_url']})")
        print(f"  Their phone  {info['family_short_url'].replace('/family', '/phone')}   (every email and text Saathi sends)")
        email = "Amazon SES" if settings.live_ses else f"SMTP ({settings.smtp_host})" if settings.live_smtp else None
        print(f"  Simulated time starts at {info['now'][11:16]}. Email: " +
              (f"sent via {email}" if email else "demo copies only") + "; texts: " +
              ("sent via Amazon SNS" if settings.live_sns else "demo copies only") +
              f". Copies in {settings.outbox_dir}")
        print("Press Ctrl+C to stop.\n")
        if not args.no_browser:
            webbrowser.open(info["elder_url"])
            # Open the token-free link; the host redirects it to family.html?token=...
            # (on some Windows setups the query string is dropped when opening a URL).
            webbrowser.open(info["family_short_url"])
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print("A process exited; shutting down.")
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
