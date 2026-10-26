"""
Webhook that prints every message POSTed to it by the CPI iFlow's HTTP receiver adapter.

Runs on SAP BTP Cloud Foundry (cf push) - the printed messages appear in the app logs:
    cf logs em-webhook

Locally:
    python webhook.py
"""

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Webhook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        try:
            body = json.dumps(json.loads(body), indent=2)
        except ValueError:
            pass
        print(f"\n===== Message received {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{body}\n", flush=True)
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        # Lets you check the app URL in a browser
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Webhook is running. CPI should POST messages to this URL.")

    def log_message(self, *args):
        pass


def main():
    port = int(os.getenv("PORT", "8000"))  # Cloud Foundry sets PORT
    server = ThreadingHTTPServer(("0.0.0.0", port), Webhook)
    print(f"Webhook listening on port {port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
