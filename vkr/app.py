"""Локальный сервер ВКР. Запуск: python app.py"""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from generator import generate

ROOT = Path(__file__).resolve().parent
SITE = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))
FIELDS = {"target": "Целевое население", "seats": "Места в школе", "seed": "Номер варианта"}


class Handler(SimpleHTTPRequestHandler):
    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/site.json":
            self.send_json(SITE)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path != "/generate":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Ожидаются три числовых параметра.")
            params = json.loads(self.rfile.read(size))
            if not isinstance(params, dict):
                raise ValueError("Параметры должны быть объектом.")
            for field, label in FIELDS.items():
                value = params.get(field)
                if type(value) is not int or not 0 <= value <= 1_000_000:
                    raise ValueError(f"{label} — целое число от 0 до 1 000 000.")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json({"error": "Ожидается JSON в кодировке UTF-8."}, 400)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        self.send_json(generate(SITE, **{key: params[key] for key in FIELDS}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port),
                             partial(Handler, directory=str(ROOT / "web"))) as server:
        print(f"Квартал: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
