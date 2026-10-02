"""A local-only server for testing the load runner without a remote target."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Demo(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = self.path.split('?')[0]
        code = 200
        if path == '/openapi.json':
            body = {'openapi': '3.0.3', 'info': {'title': 'Demo', 'version': '1'}, 'paths': {p: {'get': {'summary': s}} for p, s in [('/health', 'Health check'), ('/api/items', 'Danh sách mẫu'), ('/slow', 'Chậm 250ms'), ('/busy', '503: kiểm tra tự dừng'), ('/limited', '429: kiểm tra tự dừng')]}}
        elif path == '/health':
            body = {'status': 'ok'}
        elif path == '/api/items':
            body = {'items': [{'id': 1, 'name': 'Demo item'}]}
        elif path == '/slow':
            time.sleep(.25)
            body = {'delay_ms': 250}
        elif path in ('/busy', '/limited', '/error'):
            code = {'/busy': 503, '/limited': 429, '/error': 500}[path]
            body = {'error': 'Demo response'}
        else:
            code, body = 404, {'error': 'Not found'}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    print('Demo server: http://127.0.0.1:8090', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8090), Demo).serve_forever()
