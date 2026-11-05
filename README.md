# Parrot Workflow

Bảng Kanban cho lộ trình Research → Test → Soft launch của Parrot. Team đăng nhập bằng Google, nộp link đầu ra, Project Manager duyệt hoặc trả về, chốt các cổng C0–C3.

- Backend: Python (Flask), chạy bằng gunicorn.
- Dữ liệu: Postgres trên Render. Khi chạy ở máy (không có `DATABASE_URL`), app tự dùng file SQLite `local.db`.
- Giao diện: `static/index.html` (một file), logo ở `static/logo.jpg`.

## Cấu trúc

| File | Vai trò |
|---|---|
| `app.py` | API, đăng nhập Google, phân quyền, lưu dữ liệu |
| `static/index.html` | Toàn bộ giao diện |
| `render.yaml` | Cấu hình Render: 1 web service + 1 database Postgres |
| `requirements.txt` | Thư viện Python |
| `.env.example` | Danh sách biến môi trường cần điền |

## Biến môi trường

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `GOOGLE_CLIENT_ID` | Có | OAuth Client ID loại *Web application* từ Google Cloud |
| `PM_EMAILS` | Có | Email Google của Project Manager, nhiều email thì cách nhau bằng dấu phẩy |
| `SESSION_SECRET` | Có | Render tự tạo khi deploy bằng `render.yaml` |
| `DATABASE_URL` | Có trên Render | Render tự điền từ database |
| `DEV_LOGIN` | Không | Chỉ dùng khi chạy ở máy: `1` cho phép đăng nhập thử bằng email, không cần Google. Bị bỏ qua trên Render |

Thành viên thường không cần khai báo trong biến môi trường: Project Manager thêm email của họ ngay trong trang (tab **Quy tắc** → **Thành viên được đăng nhập**).

## Deploy lên Render

### Bước 1. Tạo Google OAuth Client ID
1. Vào <https://console.cloud.google.com/> và tạo một project, ví dụ `parrot-workflow`.
2. **APIs & Services → OAuth consent screen**: chọn *External*, điền tên app và email hỗ trợ. Ở mục *Test users*, thêm email của cả team. Hoặc bấm *Publish app* để mọi tài khoản Google đều đăng nhập được; app vẫn chỉ cho vào những email có trong danh sách thành viên.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized JavaScript origins: `https://parrot-workflow.onrender.com`. Đây là tên dự kiến; nếu Render cấp địa chỉ khác thì sửa lại ở bước 4.
4. Chép **Client ID**, dạng `xxxx.apps.googleusercontent.com`.

### Bước 2. Đưa code lên GitHub
Máy hiện chưa cài git, nên cách nhanh nhất là upload qua trình duyệt:
1. Tạo repository mới trên GitHub, ví dụ `parrot-workflow`. Nên để **Private**.
2. Bấm **uploading an existing file**, kéo **toàn bộ nội dung bên trong** thư mục `workflow-web` vào: `app.py`, `render.yaml`, `requirements.txt`, `README.md`, `.gitignore`, `.env.example` và thư mục `static`. `render.yaml` phải nằm ở gốc repo.
3. Bấm **Commit changes**.

### Bước 3. Tạo service trên Render
1. Vào <https://dashboard.render.com/> → **New → Blueprint** → kết nối GitHub → chọn repo `parrot-workflow`.
2. Render đọc `render.yaml` và tạo service `parrot-workflow` cùng database `parrot-workflow-db`.
3. Điền hai biến Render hỏi:
   - `GOOGLE_CLIENT_ID`: Client ID ở bước 1
   - `PM_EMAILS`: email Google của Project Manager
4. Bấm **Apply** và chờ build xong (vài phút). Mở `https://<tên-service>.onrender.com/healthz`, thấy chữ `ok` là chạy.

### Bước 4. Hoàn tất
1. Nếu địa chỉ Render khác với địa chỉ đã khai ở bước 1, thêm địa chỉ thật vào *Authorized JavaScript origins* rồi đợi vài phút.
2. Project Manager mở trang, đăng nhập Google, vào tab **Quy tắc** → **Thành viên được đăng nhập** và thêm email của từng người.
3. Gửi link cho team.

## Lưu ý về gói miễn phí của Render
- **Database miễn phí có hạn dùng**: theo chính sách hiện tại của Render, Postgres gói free bị xóa sau một thời gian ngắn (khoảng 30 ngày). Nếu dùng thật, nâng database lên gói trả phí trước khi hết hạn để không mất dữ liệu. Kiểm tra lại trên trang giá của Render.
- **Web service miễn phí sẽ "ngủ"** khi không có ai truy cập. Lần mở đầu tiên sau đó phải chờ khoảng một phút. Gói trả phí thấp nhất không bị ngủ.

## Chạy thử ở máy

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DEV_LOGIN=1
set PM_EMAILS=pm@test.local
python app.py
```

Mở <http://127.0.0.1:5000>, nhập `pm@test.local` vào ô đăng nhập thử. Dữ liệu lưu trong `local.db` (đã nằm trong `.gitignore`).

## Phân quyền
- **Project Manager** (email trong `PM_EMAILS`): duyệt hoặc trả về đầu ra, mở lại việc đã xong, chốt cổng, sửa đội ngũ, thêm và xóa thành viên.
- **Thành viên** (email do PM thêm): bắt đầu việc, nộp link, rút lại bài nộp.
- Mọi quyền đều được kiểm tra ở máy chủ, không chỉ ở giao diện.
