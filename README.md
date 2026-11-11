# Parrot Workflow

Bảng Kanban cho lộ trình Research → Test → Soft launch của Parrot. Team đăng nhập bằng tên và mật khẩu chung, nộp link đầu ra; Project Manager duyệt hoặc trả về và chốt các cổng C0–C3.

- Backend: Python (Flask), chạy bằng gunicorn.
- Dữ liệu: Postgres trên Render. Khi chạy ở máy (không có `DATABASE_URL`), app tự dùng file SQLite `local.db`.
- Giao diện: `static/index.html`, logo ở `static/logo.jpg`.

## Đăng nhập và phân quyền

Có hai mật khẩu, do bạn đặt khi tạo service trên Render:

| Mật khẩu | Ai dùng | Được làm gì |
|---|---|---|
| `PM_PASSWORD` | Project Manager | Mọi việc của thành viên, cộng thêm duyệt, trả về, mở lại việc, chốt cổng, sửa tên đội ngũ |
| `TEAM_PASSWORD` | Cả team | Bắt đầu việc, nộp link đầu ra, rút lại bài nộp |

- Mỗi người tự gõ tên khi đăng nhập. Tên đó hiện trong lịch sử thay đổi.
- Phiên đăng nhập giữ trong 30 ngày.
- Đổi một mật khẩu trên Render thì những ai đang dùng mật khẩu cũ bị đăng xuất.
- Nhập sai quá 20 lần trong 10 phút từ cùng một mạng thì bị khóa tạm 10 phút.
- Mọi quyền đều được kiểm tra ở máy chủ.

## Deploy lên Render

### Bước 1. Đưa code lên GitHub
1. Đăng nhập <https://github.com>, bấm **+** → **New repository**.
2. Tên `parrot-workflow`, chọn **Private**, **không** tick *Add a README file* → **Create repository**.
3. Bấm dòng **uploading an existing file**.
4. Giải nén file zip. Mở thư mục vừa giải nén, chọn tất cả (Ctrl + A), **kéo các file bên trong** vào trình duyệt, kể cả thư mục `static`.
5. Kiểm tra danh sách có `app.py`, `render.yaml`, `requirements.txt`, `README.md`, `.gitignore`, `.env.example`, `static/index.html`, `static/logo.jpg` → **Commit changes**.
6. Ở trang chính của repo, `render.yaml` phải nằm ngay ngoài cùng, không nằm trong thư mục con.

### Bước 2. Tạo service trên Render
1. Vào <https://dashboard.render.com>, chọn **Sign up with GitHub**.
2. **New +** → **Blueprint** → **Connect GitHub** → cho phép Render truy cập repo `parrot-workflow` → **Connect**.
3. **Blueprint Name:** `parrot-workflow`, **Branch:** `main`.
4. Render liệt kê web service `parrot-workflow` và database `parrot-workflow-db`, rồi hỏi 2 biến:
   - `PM_PASSWORD`: mật khẩu riêng của Project Manager
   - `TEAM_PASSWORD`: mật khẩu chung cho cả team

   Đặt hai mật khẩu khác nhau, mỗi cái ít nhất 10 ký tự.
5. Bấm **Apply** / **Deploy Blueprint**, chờ 3–5 phút. Trong tab **Logs**, khi thấy *Your service is live* là app đã chạy.
6. Địa chỉ trang nằm ở đầu trang service, dạng `https://parrot-workflow.onrender.com`. Mở `/healthz` ở địa chỉ đó, thấy chữ `ok` là app chạy được.

### Bước 3. Dùng
1. Project Manager mở trang, nhập tên và `PM_PASSWORD`.
2. Gửi link trang và `TEAM_PASSWORD` cho team qua kênh riêng (Zalo, tin nhắn), không đăng công khai.
3. Đổi mật khẩu sau này: Render → service `parrot-workflow` → **Environment** → sửa giá trị → **Save, rebuild and deploy**.

## Lưu ý về gói miễn phí của Render
- **Database miễn phí có hạn dùng ngắn** (khoảng 30 ngày theo chính sách hiện tại của Render). Nếu dùng thật, vào `parrot-workflow-db` → **Upgrade** sang gói trả phí trước khi hết hạn để không mất dữ liệu. Kiểm tra lại trên trang giá của Render.
- **Web service miễn phí sẽ "ngủ"** khi không ai truy cập. Lần mở đầu tiên sau đó phải chờ khoảng một phút.

## Khi không đăng nhập được

Mở `https://<địa-chỉ-của-bạn>.onrender.com/api/health`. Trang trả về một dòng JSON:

| Thấy gì | Nguyên nhân | Cách sửa |
|---|---|---|
| `"passwordsSet": false` | Chưa đặt `PM_PASSWORD` / `TEAM_PASSWORD` | Render → service → **Environment** → thêm 2 biến → **Save, rebuild and deploy** |
| `"db": "error: ..."` | Database không kết nối được (gói miễn phí đã hết hạn hoặc bị xóa) | Render → `parrot-workflow-db`: kiểm tra trạng thái, nâng cấp hoặc tạo lại database |
| Không mở được trang, hoặc chờ rất lâu | Service miễn phí đang "ngủ" hoặc deploy lỗi | Đợi 1 phút; nếu vẫn lỗi, xem tab **Logs** |

Thông báo trên màn hình đăng nhập và cách xử lý:

| Thông báo | Cách xử lý |
|---|---|
| Mật khẩu không đúng | Dùng đúng mật khẩu đã đặt trên Render. `pm123` / `team123` chỉ dùng khi chạy thử ở máy |
| Máy chủ chưa đặt PM_PASSWORD và TEAM_PASSWORD | Như dòng đầu bảng trên |
| Sai mật khẩu quá nhiều lần | Đợi 10 phút |
| Mật khẩu đúng nhưng trình duyệt không lưu phiên đăng nhập | Bật cookie cho trang, không dùng chế độ chặn cookie |
| Mật khẩu đúng nhưng trang đang mở bên trong khung nhúng | Mở trực tiếp địa chỉ `.onrender.com`, không nhúng vào Google Sites, Notion… |
| Máy chủ đang lỗi hoặc đang khởi động (mã 5xx) | Mở `/api/health` và tab **Logs** trên Render |

Địa chỉ thật của service có thể có thêm đuôi (ví dụ `parrot-workflow-abcd.onrender.com`) nếu tên `parrot-workflow` đã có người dùng. Lấy địa chỉ ở đầu trang service trên Render.

## Chạy thử ở máy

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mở <http://127.0.0.1:5000>. Khi chưa đặt biến môi trường, mật khẩu thử là `pm123` (Project Manager) và `team123` (thành viên). Dữ liệu lưu trong `local.db`.

## Cấu trúc

| File | Vai trò |
|---|---|
| `app.py` | API, đăng nhập, phân quyền, lưu dữ liệu |
| `static/index.html` | Toàn bộ giao diện |
| `render.yaml` | Cấu hình Render: 1 web service + 1 database Postgres |
| `requirements.txt` | Thư viện Python |
| `.env.example` | Danh sách biến môi trường |
