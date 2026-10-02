# URL Load Test — giao diện đơn giản

Chỉ có 2 ô: **URL đang deploy**, **Số lượng request** và một nút **Start**. Khi đang chạy, nút đổi thành **Stop**. Kết quả hiển thị ngay phía dưới.

Lab gửi GET từ máy chủ lab tới URL public (Vercel, Render, …). **Không host lab trên Vercel**: Vercel là serverless, không giữ process/thread 300 giây. Host lab trên Render, Railway, Fly.io hoặc VPS, rồi dán URL app đang deploy.

## Online (Render / Railway / Docker)

Nền tảng cần process Python sống lâu và biến `PORT`.

- Render: Web Service, runtime Python, start `python app.py` (có `Procfile`).
- Railway: deploy repo, dùng `Procfile`.
- Docker: `docker compose up --build` rồi mở cổng 8088.

Mở URL lab trên internet → dán `https://your-app.vercel.app/` (hoặc path cụ thể) → Start.

Mặc định **chặn localhost và IP nội bộ**. Chỉ GET, không chạy JavaScript, không POST/login. Tùy chọn `ALLOWED_HOSTS=your-lab.onrender.com` để khóa Host header.

## Windows (máy local)

Python >= 3.11. `start.bat` hoặc:

```powershell
py -3 app.py
```

Mở http://127.0.0.1:8088. Demo local:

```powershell
$env:ALLOW_PRIVATE_TARGETS="1"
py -3 app.py
```

Terminal khác: `py -3 demo_server.py`, rồi nhập `http://127.0.0.1:8090/health`.

## Hoạt động

- Tổng số request 1–1000, tối đa 10 request/giây, 5 request đồng thời.
- Timeout 5 giây/request, tối đa 300 giây/lượt. HTTP 429/503 hoặc ≥20% lỗi trên 20 request gần nhất thì tự dừng.
- Không follow redirect, không cookie/token trên UI (API login có thể 401/403).
- Report: `/report.json`, `/report.csv`, health: `/health`.

## Kiểm thử

```powershell
py -3 -m unittest -v
```
