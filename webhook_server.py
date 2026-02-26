"""
webhook_server.py - Lightweight HTTP webhook server
n8n calls this to trigger the pipeline on the host.
Runs as a systemd service on the VPS.

Endpoints:
  POST /run          - Run full pipeline
  POST /run?dry=true - Dry run (no uploads)
  GET  /health       - Health check
  GET  /status       - Last run result
"""
import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ── Config ─────────────────────────────────────────────────────────
PORT = 8765
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_BIN = os.path.join(PROJECT_DIR, "venv", "bin", "python3")
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "webhook.log")

os.makedirs(os.path.join(PROJECT_DIR, "logs"), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger("webhook")

# ── State ───────────────────────────────────────────────────────────
state = {
    "running": False,
    "last_run": None,
    "last_result": None,
}


def run_pipeline(dry_run: bool = False):
    """Execute main.py in a background thread."""
    if state["running"]:
        logger.warning("Pipeline already running — skipping")
        return

    state["running"] = True
    state["last_run"] = datetime.utcnow().isoformat()

    args = [PYTHON_BIN, "main.py"]
    if dry_run:
        args.append("--dry-run")

    logger.info(f"Starting pipeline: {' '.join(args)}")

    try:
        result = subprocess.run(
            args,
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
            env={**os.environ, "PYTHONPATH": PROJECT_DIR},
        )
        # Parse JSON result from stdout
        output = result.stdout.strip()
        try:
            # Find last JSON block in output
            lines = output.split("\n")
            json_str = ""
            brace_depth = 0
            capturing = False
            for line in reversed(lines):
                for ch in reversed(line):
                    if ch == "}":
                        brace_depth += 1
                        capturing = True
                    if capturing:
                        json_str = ch + json_str
                    if ch == "{":
                        brace_depth -= 1
                    if capturing and brace_depth == 0:
                        break
                if capturing and brace_depth == 0:
                    break
                if capturing:
                    json_str = "\n" + json_str
            parsed = json.loads(json_str)
        except Exception:
            parsed = {"success": result.returncode == 0, "raw_output": output[-2000:]}

        state["last_result"] = parsed
        logger.info(f"Pipeline finished: success={parsed.get('success')}")

    except subprocess.TimeoutExpired:
        state["last_result"] = {"success": False, "error": "Pipeline timed out after 10 minutes"}
        logger.error("Pipeline timed out")
    except Exception as e:
        state["last_result"] = {"success": False, "error": str(e)}
        logger.error(f"Pipeline error: {e}")
    finally:
        state["running"] = False


class WebhookHandler(BaseHTTPRequestHandler):

    def _auth(self) -> bool:
        """Check webhook secret if configured."""
        if not WEBHOOK_SECRET:
            return True
        token = self.headers.get("X-Webhook-Secret", "")
        return token == WEBHOOK_SECRET

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send(200, {"status": "ok", "running": state["running"]})

        elif parsed.path == "/status":
            self._send(200, {
                "running": state["running"],
                "last_run": state["last_run"],
                "last_result": state["last_result"],
            })
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/build-catalog":
            if not self._auth():
                self._send(401, {"error": "Unauthorized"})
                return
            def _build():
                try:
                    from scripts.catalog_builder import build_catalog
                    catalog = build_catalog()
                    logger.info(f"[webhook] Catalog built: {catalog['product_count']} products, {catalog['total_api_calls']} API calls")
                except Exception as e:
                    logger.error(f"[webhook] Catalog build failed: {e}")
            threading.Thread(target=_build, daemon=True).start()
            self._send(202, {"status": "accepted", "message": "Catalog build started."})
            return

        if parsed.path == "/catalog-status":
            try:
                from scripts.catalog_builder import catalog_status
                self._send(200, catalog_status())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        if parsed.path != "/run":
            self._send(404, {"error": "Not found"})
            return

        if not self._auth():
            self._send(401, {"error": "Unauthorized"})
            return

        if state["running"]:
            self._send(409, {"error": "Pipeline already running", "started_at": state["last_run"]})
            return

        qs = parse_qs(parsed.query)
        dry_run = qs.get("dry", ["false"])[0].lower() == "true"

        # Fire pipeline in background thread — return immediately to n8n
        thread = threading.Thread(target=run_pipeline, args=(dry_run,), daemon=True)
        thread.start()

        self._send(202, {
            "status": "accepted",
            "dry_run": dry_run,
            "message": "Pipeline started. Poll /status for results.",
        })

    def log_message(self, format, *args):
        logger.info(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    logger.info(f"Webhook server starting on port {PORT}")
    logger.info(f"Project dir: {PROJECT_DIR}")
    logger.info(f"Secret auth: {'enabled' if WEBHOOK_SECRET else 'DISABLED — set WEBHOOK_SECRET in .env!'}")
    server = HTTPServer(("127.0.0.1", PORT), WebhookHandler)
    server.serve_forever()
