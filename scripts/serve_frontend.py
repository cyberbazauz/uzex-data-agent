"""Minimal same-origin server for a public demo.

Serves the production React build and proxies ``/api/`` to the FastAPI backend.
This mirrors the nginx deployment without requiring Docker, and is only intended
for demo/exposure when nginx is unavailable.
"""

from __future__ import annotations

import argparse
import http.client
import mimetypes
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "frontend" / "dist"

SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
PROXY_RESPONSE_HEADERS = (
    "Content-Type",
    "X-Request-ID",
    "Retry-After",
)
PROXY_REQUEST_HEADERS = ("Content-Type", "X-Request-ID", "Accept")


class Handler(BaseHTTPRequestHandler):
    server_version = "UZEXDemo/1.0"

    def _api_target(self) -> tuple[str, int]:
        target = urlsplit(self.server.api_base)  # type: ignore[attr-defined]
        return target.hostname or "127.0.0.1", target.port or 80

    def _proxy(self) -> None:
        host, port = self._api_target()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {}
        for name in PROXY_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value:
                headers[name] = value
        headers.setdefault("Accept", "application/json")

        connection = http.client.HTTPConnection(host, port, timeout=30)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except Exception as error:  # noqa: BLE001
            self.log_error("proxy error: %s", error)
            payload = b'{"detail":"backend unavailable"}'
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
        else:
            self.send_response(response.status)
            for name in PROXY_RESPONSE_HEADERS:
                value = response.getheader(name)
                if value:
                    self.send_header(name, value)
        finally:
            connection.close()

        for name, value in SAFE_HEADERS.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in {"", "/"}:
            path = "/index.html"
        relative = posixpath.normpath(path).lstrip("/")
        candidate = (self.server.dist_dir / relative).resolve()  # type: ignore[attr-defined]
        dist = self.server.dist_dir.resolve()  # type: ignore[attr-defined]
        try:
            candidate.relative_to(dist)
        except ValueError:
            self._not_found()
            return

        if not candidate.is_file():
            if "." in posixpath.basename(relative):
                self._not_found()
                return
            candidate = dist / "index.html"
        if not candidate.is_file():
            self._not_found()
            return

        payload = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
            "image/svg+xml",
        }:
            content_type += "; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        for name, value in SAFE_HEADERS.items():
            self.send_header(name, value)
        if path.startswith("/assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        elif candidate.name == "index.html":
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _not_found(self) -> None:
        payload = b"Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self._static()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self._not_found()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(
            f"{self.address_string()} - {format % args}\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the built UZEX frontend + API proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    if not (args.dist / "index.html").is_file():
        print(f"frontend build not found: {args.dist}\\index.html", file=sys.stderr)
        print("run `npm run build` in frontend first", file=sys.stderr)
        return 2

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.dist_dir = args.dist  # type: ignore[attr-defined]
    server.api_base = args.api  # type: ignore[attr-defined]
    print(f"serving {args.dist} on http://{args.host}:{args.port} (api {args.api})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
