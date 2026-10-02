"""Bounded, single-machine HTTP load runner. Python 3.11+, stdlib only."""
import concurrent.futures
import http.client
import ipaddress
import math
import os
import socket
import ssl
import threading
import time
from collections import Counter
from urllib.parse import urlsplit

_BLOCKED_NAMES = {
    'localhost', 'localhost.localdomain', 'metadata', 'metadata.google.internal',
    'kubernetes.default', 'kubernetes.default.svc',
}


def allow_private_targets():
    return os.getenv('ALLOW_PRIVATE_TARGETS', '').lower() in ('1', 'true', 'yes')


def resolve_public_ip(hostname, port):
    host = (hostname or '').rstrip('.').lower()
    if not host:
        raise ValueError('URL thiếu hostname.')
    if not allow_private_targets():
        if host in _BLOCKED_NAMES or host.endswith(('.local', '.internal', '.localhost', '.lan')):
            raise ValueError('Chỉ test URL public đang deploy, không dùng localhost hay tên nội bộ.')
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or 0, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError('Không phân giải được DNS của URL.') from exc
        candidates = []
        for info in infos:
            addr = info[4][0]
            if addr not in candidates:
                candidates.append(addr)
        if not candidates:
            raise ValueError('Không phân giải được DNS của URL.')
    if allow_private_targets():
        return candidates[0]
    for addr in candidates:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError('Chỉ test URL public đang deploy, không gọi IP nội bộ hay loopback.')
    return candidates[0]


def origin_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('URL phải là http(s)://host[:port], không chứa tài khoản.')
    if p.path not in ('', '/') or p.query or p.fragment:
        raise ValueError('Base URL chỉ chứa origin; chuyển đường dẫn xuống ô endpoint.')
    _ = p.port
    resolve_public_ip(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    return p.scheme + '://' + p.netloc


def endpoint_path(value):
    p = urlsplit(value)
    if not value.startswith('/') or value.startswith('//') or p.scheme or p.netloc or p.fragment or '\\' in value:
        raise ValueError('Endpoint phải là đường dẫn cùng server, bắt đầu bằng /.')
    if any(ord(c) < 33 or ord(c) > 126 for c in value) or '{' in value or '}' in value:
        raise ValueError('Thay {id} bằng giá trị thật; URL-encode dấu cách và tiếng Việt.')
    return value


def _host_header(hostname, port, scheme):
    default = 443 if scheme == 'https' else 80
    host = hostname
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.version == 6:
            host = f'[{hostname}]'
    except ValueError:
        pass
    if not port or port == default:
        return host
    return f'{host}:{port}'


def request(base, path, token='', timeout=5, limit=2_000_000, peer=None):
    p = urlsplit(base)
    port = p.port or (443 if p.scheme == 'https' else 80)
    hostname = p.hostname
    peer = peer or resolve_public_ip(hostname, port)
    if p.scheme == 'https':
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(peer, port, timeout=timeout, context=ctx)

        def connect():
            sock = socket.create_connection((peer, port), timeout)
            conn.sock = ctx.wrap_socket(sock, server_hostname=hostname)

        conn.connect = connect
    else:
        conn = http.client.HTTPConnection(peer, port, timeout=timeout)
    if '\r' in token or '\n' in token:
        raise ValueError('Token không hợp lệ')
    headers = {
        'Host': _host_header(hostname, port, p.scheme),
        'User-Agent': 'ServerLoadLab/1.0',
        'Accept': 'application/json,text/html;q=0.9,*/*;q=0.8',
        'Connection': 'close',
    }
    if token:
        headers['Authorization'] = 'Bearer ' + token
    started = time.monotonic()
    # Closing the socket enforces a wall-clock deadline as well as socket timeout.
    def abort():
        try:
            if conn.sock:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('response_too_large')
        return response.status, body, (time.monotonic() - started) * 1000
    finally:
        timer.cancel()
        conn.close()


class Runner:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.running = False
        self.rows = []
        self.config = {}
        self.reason = 'Chưa chạy'
        self.started = self.ended = 0
        self.skipped = self.sent = 0
        self.peer = None

    def start(self, c):
        if 'url' in c:
            raw = str(c['url']).strip()
            u = urlsplit(raw)
            if u.fragment:
                raise ValueError('Bỏ phần #fragment khỏi URL.')
            base = origin_url(u.scheme + '://' + u.netloc)
            path = endpoint_path((u.path or '/') + ('?' + u.query if u.query else ''))
            if isinstance(c.get('count'), bool) or not str(c.get('count', '')).isdigit():
                raise ValueError('Số request phải là số nguyên từ 1 đến 1000.')
            count = int(c['count'])
            if not 1 <= count <= 1000:
                raise ValueError('Số request phải từ 1 đến 1000.')
            c = dict(base=base, paths=path, count=count, rps=10, workers=5,
                     duration=300, timeout=5, error_limit=20, authorized=True)
        base = origin_url(c['base'])
        paths = [endpoint_path(p.strip()) for p in c['paths'].splitlines() if p.strip()]
        if not paths or len(paths) > 100:
            raise ValueError('Chọn 1–100 endpoint.')
        clean = {'base': base, 'paths': paths, 'count': c.get('count', 0)}
        for k, lo, hi in [('rps', 1, 100), ('workers', 1, 30), ('duration', 1, 300), ('timeout', 1, 15), ('error_limit', 1, 100)]:
            n = int(c[k])
            if not lo <= n <= hi:
                raise ValueError(f'{k}: từ {lo} đến {hi}')
            clean[k] = n
        if c.get('authorized') is not True:
            raise ValueError('Xác nhận quyền kiểm thử server trước khi chạy.')
        token = str(c.get('token', '')).strip()
        if '\r' in token or '\n' in token:
            raise ValueError('Token không hợp lệ')
        with self.lock:
            if self.running:
                raise ValueError('Một bài test đang chạy; dừng và đợi hoàn tất trước.')
            parsed = urlsplit(base)
            self.peer = resolve_public_ip(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
            self.config, self.rows = clean, []
            self.stop_event.clear()
            self.running = True
            self.reason = 'Đang chạy'
            self.started = time.monotonic()
            self.ended = self.sent = self.skipped = 0
            threading.Thread(target=self._run, args=(token,), daemon=True).start()

    def stop(self, reason='Đã bấm dừng'):
        with self.lock:
            if self.running and not self.stop_event.is_set():
                self.reason = reason
                self.stop_event.set()

    def _one(self, path, token):
        if self.stop_event.is_set():
            return
        t = time.monotonic()
        with self.lock:
            self.sent += 1
        status, error = 0, ''
        try:
            status, _, ms = request(self.config['base'], path, token, self.config['timeout'], peer=self.peer)
        except Exception as exc:
            ms = (time.monotonic() - t) * 1000
            error = type(exc).__name__
        ok = 200 <= status < 300
        with self.lock:
            self.rows.append({'second': round(t - self.started, 3), 'path': path, 'status': status, 'ms': round(ms, 2), 'ok': ok, 'error': error})
            recent = self.rows[-20:]
            if status in (429, 503):
                self.stop(f'Tự dừng: HTTP {status}')
            elif len(recent) == 20 and 100 * sum(not x['ok'] for x in recent) / 20 >= self.config['error_limit']:
                self.stop('Tự dừng: tỷ lệ lỗi của 20 request gần nhất vượt ngưỡng')

    def _run(self, token):
        c = self.config
        pending = set()
        next_at = self.started
        i = 0
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=c['workers']) as pool:
                while not self.stop_event.is_set() and time.monotonic() < self.started + c['duration'] and (not c['count'] or i < c['count']):
                    if self.stop_event.wait(max(0, next_at - time.monotonic())):
                        break
                    if time.monotonic() >= self.started + c['duration']:
                        break
                    pending = {f for f in pending if not f.done()}
                    if len(pending) < c['workers']:
                        pending.add(pool.submit(self._one, c['paths'][i % len(c['paths'])], token))
                        i += 1
                    else:
                        with self.lock:
                            self.skipped += 1
                    # No catch-up bursts and no unbounded task queue.
                    next_at = time.monotonic() + 1 / c['rps']
                with self.lock:
                    if not self.stop_event.is_set():
                        self.reason = 'Hết thời gian; đang đợi request còn lại'
        finally:
            with self.lock:
                self.ended = time.monotonic()
                self.running = False
                if not self.stop_event.is_set():
                    self.reason = ('Hết giới hạn 300 giây trước khi đủ số request' if c['count'] and self.sent < c['count'] else 'Hoàn tất')

    def snapshot(self, include_rows=False):
        with self.lock:
            rows = list(self.rows)
            elapsed = max(0.001, (self.ended or time.monotonic()) - self.started) if self.started else 0
            def stats(items):
                times = sorted(r['ms'] for r in items)
                n = len(times)
                return {'count': n, 'errors': sum(not r['ok'] for r in items), 'avg_ms': round(sum(times) / n, 2) if n else 0,
                        'p50_ms': times[math.ceil(n * .5) - 1] if n else 0, 'p95_ms': times[math.ceil(n * .95) - 1] if n else 0,
                        'p99_ms': times[math.ceil(n * .99) - 1] if n else 0}
            result = {'running': self.running, 'reason': self.reason, 'elapsed': round(elapsed, 2), 'sent': self.sent,
                      'skipped': self.skipped, 'actual_rps': round(len(rows) / elapsed, 2) if elapsed else 0,
                      'config': self.config, 'summary': stats(rows), 'statuses': dict(Counter(str(r['status']) for r in rows)),
                      'endpoints': {p: stats([r for r in rows if r['path'] == p]) for p in self.config.get('paths', [])}}
            if include_rows:
                result['requests'] = rows
            return result
