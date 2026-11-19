"""Design preview of the Idavazhi website, on this computer only.

Serves the files in this folder first and falls back to the live site's
files in ../web for pictures, icons and the video. It listens on 127.0.0.1
only, so the public tunnel (which points at the real server) never shows it.
"Open Idavazhi" goes to the real app on port 8001.

    python design-preview/serve_preview.py [port]
"""
import http.server
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
PAGES = {"/privacy": "privacy.html", "/terms": "terms.html", "/refunds": "refunds.html", "/contact": "contact.html"}
# The live server's policy, so anything it would block is blocked here too.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


class Preview(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path in ("", "/"):
            path = "/index.html"
        path = "/" + PAGES.get(path, path.lstrip("/"))
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            return os.path.join(HERE, "missing")
        for base in (HERE, WEB):
            full = os.path.join(base, rel)
            if os.path.isfile(full):
                return full
        return os.path.join(HERE, "missing")

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/app":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8001/app")
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), Preview) as srv:
        print("Design preview: http://127.0.0.1:%d/" % port, flush=True)
        srv.serve_forever()
