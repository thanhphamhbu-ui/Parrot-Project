# Parrot Workflow

Bảng Kanban cho lộ trình Research → Test → Soft launch của Parrot. Team đăng nhập bằng tên và mật khẩu chung, nộp link đầu ra; Project Manager duyệt hoặc trả về và chốt các cổng C0–C3.

- Backend: Python (Flask), chạy bằng gunicorn.
- Dữ liệu: Postgres trên Render. Khi chạy ở máy (không có `DATABASE_URL`), app tự dùng file SQLite `local.db`.
- Giao diện: `static/index.html`, logo ở `static/logo.jpg`.

## Đăng nhập và phân quyền

| Mật khẩu | Ai dùng | Được làm gì |
|---|---|---|
| `PM_PASSWORD` | Project Manager | Mọi việc của thành viên, cộng thêm duyệt, trả về, mở lại việc, chốt cổng, sửa tên đội ngũ |
| `TEAM_PASSWORD` | Cả team | Bắt đầu việc, nộp link đầu ra, rút lại bài nộp |

- Mỗi người tự gõ tên khi đăng nhập. Tên đó hiện trong lịch sử thay đổi.
- Phiên đăng nhập giữ trong 30 ngày. Đổi một mật khẩu thì mọi người phải đăng nhập lại.
- Nhập sai quá 20 lần trong 10 phút từ cùng một mạng thì bị khóa tạm 10 phút.

## Deploy lên Render bằng Web Service

### Bước 1. Đưa code lên GitHub
1. Tạo repo **Private** tên `parrot-workflow` (không tick *Add a README file*).
2. Bấm **uploading an existing file**, giải nén file zip, kéo **toàn bộ nội dung bên trong** vào trình duyệt, kể cả thư mục `static` và các file bắt đầu bằng dấu chấm.
3. Ở trang chính của repo phải thấy ngay: `app.py`, `gunicorn.conf.py`, `requirements.txt`, `.python-version`, `README.md`, thư mục `static` (bên trong có `index.html`, `logo.jpg`). → **Commit changes**.

> Nếu GitHub không nhận file `.python-version` khi kéo thả: bấm **Add file → Create new file**, đặt tên `.python-version`, nội dung `3.12.7`, commit.

### Bước 2. Tạo database Postgres
1. Render → **New +** → **Postgres**.
2. **Name:** `parrot-workflow-db`, **Region:** Singapore, **Plan:** Free → **Create Database**.
3. Đợi trạng thái **Available**, mở mục **Connections**, copy **Internal Database URL** (dạng `postgresql://...`).

### Bước 3. Tạo Web Service
1. Render → **New +** → **Web Service** → chọn repo `parrot-workflow`.
2. Điền đúng như sau:

| Ô | Giá trị |
|---|---|
| Name | `parrot-workflow` |
| Region | Singapore (cùng region với database) |
| Branch | `main` |
| Root Directory | để trống |
| Runtime / Language | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app` |
| Instance Type | Free |

3. Mục **Environment Variables**, thêm:

| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL vừa copy ở bước 2 |
| `PM_PASSWORD` | Mật khẩu Project Manager, ít nhất 10 ký tự |
| `TEAM_PASSWORD` | Mật khẩu team, khác mật khẩu PM |

   `SESSION_SECRET` là tùy chọn, bỏ trống vẫn chạy.
4. Mở **Advanced** → **Health Check Path**: `/healthz`.
5. Bấm **Create Web Service**, chờ 3–5 phút đến khi log có dòng *Your service is live*.

### Bước 4. Kiểm tra
1. Lấy địa chỉ ở đầu trang service (có thể có thêm đuôi, ví dụ `parrot-workflow-abcd.onrender.com`).
2. Mở `<địa chỉ>/api/health`. Phải thấy `"db": "ok"`, `"passwordsSet": true` và `"database": "postgres"`.
3. Mở `<địa chỉ>`, đăng nhập bằng tên và `PM_PASSWORD`.
4. Gửi link trang và `TEAM_PASSWORD` cho team qua kênh riêng.

Đổi mật khẩu sau này: service → **Environment** → sửa giá trị → **Save, rebuild and deploy**.

## Khi có lỗi

| Thấy gì | Nguyên nhân | Cách sửa |
|---|---|---|
| Log: `Running '.'` hoặc `filename argument required` | Start Command sai | Settings → Build & Deploy → Start Command: `gunicorn app:app` |
| Log: `ModuleNotFoundError` hoặc `No such file app.py` | File nằm trong thư mục con trên GitHub | Đưa file ra ngoài cùng repo, hoặc đặt Root Directory đúng thư mục đó |
| `/api/health`: `"passwordsSet": false` | Thiếu `PM_PASSWORD` / `TEAM_PASSWORD` | Thêm trong Environment → Save, rebuild and deploy |
| `/api/health`: `"db": "error: ..."` | `DATABASE_URL` sai hoặc database hết hạn | Copy lại Internal Database URL; kiểm tra database còn Available |
| `/api/health`: `"database": "sqlite TẠM..."` | Chưa đặt `DATABASE_URL` | Thêm `DATABASE_URL`, nếu không dữ liệu mất mỗi lần deploy |
| Màn hình: "Mật khẩu không đúng" | Sai mật khẩu. `pm123`/`team123` chỉ dùng ở máy | Dùng mật khẩu đặt trên Render |
| Màn hình: "trình duyệt không lưu phiên" / "khung nhúng" | Cookie bị chặn hoặc trang bị nhúng | Mở trực tiếp địa chỉ `.onrender.com`, bật cookie |
| Mở trang chờ khoảng 1 phút | Gói Free "ngủ" khi không ai dùng | Bình thường, đợi rồi tải lại |

## Lưu ý về gói miễn phí của Render
- **Database Free có hạn dùng ngắn** (khoảng 30 ngày theo chính sách hiện tại, kiểm tra lại trên trang giá của Render). Dùng thật thì nâng database lên gói trả phí trước khi hết hạn để không mất dữ liệu.
- **Web service Free sẽ "ngủ"** khi không ai truy cập; lần mở đầu tiên sau đó chờ khoảng một phút.

## Chạy thử ở máy

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mở <http://127.0.0.1:5000>. Mật khẩu thử là `pm123` (Project Manager) và `team123` (thành viên). Dữ liệu lưu trong `local.db`.

## Cấu trúc

| File | Vai trò |
|---|---|
| `app.py` | API, đăng nhập, phân quyền, lưu dữ liệu |
| `gunicorn.conf.py` | Cấu hình gunicorn (cổng, số worker), để Start Command chỉ cần `gunicorn app:app` |
| `.python-version` | Chốt Python 3.12.7 trên Render |
| `static/index.html` | Toàn bộ giao diện |
| `requirements.txt` | Thư viện Python |
| `.env.example` | Danh sách biến môi trường |
