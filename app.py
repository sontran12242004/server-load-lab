import csv
import io
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from engine import Runner, origin_url, endpoint_path, request

runner = Runner()
csrf = secrets.token_urlsafe(32)
PORT = int(os.getenv('PORT', '8088'))
BIND_HOST = os.getenv('BIND_HOST', '0.0.0.0')


def host_name(raw):
    raw = (raw or '').strip().lower()
    if not raw or any(c in raw for c in '\r\n/ @'):
        return ''
    if raw.startswith('['):
        end = raw.find(']')
        return raw[1:end] if end != -1 else ''
    return raw.split(':')[0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log tokens or query strings.

    def send(self, value, content_type='application/json; charset=utf-8', code=200):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        host = host_name(self.headers.get('Host'))
        if not host:
            return False
        allowed = [h.strip().lower() for h in os.getenv('ALLOWED_HOSTS', '*').split(',') if h.strip()]
        if '*' in allowed:
            return True
        return host in {h.split(':')[0] for h in allowed}

    def token_ok(self):
        return self.headers.get('X-CSRF-Token') == csrf or self.headers.get('X-Local-Token') == csrf

    def do_GET(self):
        if self.path == '/health':
            return self.send({'ok': True})
        if not self.valid_host():
            return self.send({'error': 'Host rejected'}, code=403)
        if self.path == '/':
            page = Path(__file__).with_name('index.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
            return self.send(page.encode(), 'text/html; charset=utf-8')
        if self.path == '/api/status':
            return self.send(runner.snapshot())
        if self.path == '/report.json':
            return self.send(runner.snapshot(True))
        if self.path == '/report.csv':
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=['second', 'path', 'status', 'ms', 'ok', 'error'])
            writer.writeheader()
            # Neutralize spreadsheet formulas in user-entered paths.
            for row in runner.snapshot(True)['requests']:
                writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for k, v in row.items()})
            return self.send(('\ufeff' + out.getvalue()).encode(), 'text/csv; charset=utf-8')
        return self.send({'error': 'Not found'}, code=404)

    def do_POST(self):
        if not self.valid_host() or not self.token_ok():
            return self.send({'error': 'Request token rejected'}, code=403)
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 100_000:
                raise ValueError('Request body quá lớn hoặc trống')
            c = json.loads(self.rfile.read(size))
            if self.path == '/api/start':
                if 'url' not in c or 'count' not in c:
                    raise ValueError('Nhập URL đầy đủ và số lượng request.')
                runner.start({'url': c['url'], 'count': c['count']})
                return self.send({'ok': True})
            if self.path == '/api/stop':
                runner.stop()
                return self.send({'ok': True})
            if self.path == '/api/openapi':
                if c.get('authorized') is not True:
                    raise ValueError('Xác nhận quyền kiểm thử server trước khi đọc OpenAPI.')
                base = origin_url(c['base'])
                path = endpoint_path(c['spec_path'])
                status, body, _ = request(base, path, str(c.get('token', '')), 5)
                if status != 200:
                    raise ValueError(f'OpenAPI trả HTTP {status}; kiểm tra URL JSON và token.')
                spec = json.loads(body)
                if not isinstance(spec, dict) or not isinstance(spec.get('paths'), dict):
                    raise ValueError('Không phải OpenAPI JSON có trường paths.')
                candidates = []
                for p, methods in spec['paths'].items():
                    if isinstance(methods, dict) and 'get' in methods:
                        candidates.append({'path': p, 'summary': str(methods['get'].get('summary', '')) if isinstance(methods['get'], dict) else ''})
                return self.send({'endpoints': candidates, 'note': 'Chỉ liệt kê GET. Kiểm tra prefix servers/basePath, query bắt buộc và thay {id} trước khi chạy.'})
            return self.send({'error': 'Not found'}, code=404)
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
            return self.send({'error': message}, code=400)


if __name__ == '__main__':
    print(f'Server Load Lab: http://{BIND_HOST}:{PORT} — dán URL public đang deploy rồi Start', flush=True)
    server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        runner.stop('Ứng dụng đã đóng')
    finally:
        server.server_close()
