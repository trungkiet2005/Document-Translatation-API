# PDF Translation API

API dịch PDF với các tính năng:
- Dịch PDF từ nhiều ngôn ngữ sang tiếng Việt
- Hỗ trợ nhiều dịch vụ dịch (Google Translate, Bing, DeepL, OpenAI, Gemini)
- Tùy chỉnh font chữ và cỡ chữ
- Trích xuất văn bản và bounding boxes
- API RESTful với đầy đủ tài liệu

## Cài đặt

1. Tạo môi trường ảo:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

2. Cài đặt dependencies:
```bash
pip install -r requirements.txt
```

3. Cài đặt thêm deep-translator:
```bash
pip install deep-translator
```

## Chạy API

```bash
python app.py
```

API sẽ chạy tại `http://localhost:7860`

## API Endpoints

### 1. Trang chính
- `GET /`: Thông tin về API và các endpoints

### 2. Dịch PDF
- `POST /translate`: Dịch file PDF
  - Form-data:
    - `file`: File PDF cần dịch
    - `source_lang`: Ngôn ngữ nguồn (mặc định: 'en')
    - `target_lang`: Ngôn ngữ đích (mặc định: 'vi')
    - `service`: Dịch vụ dịch (mặc định: 'google')
    - `threads`: Số luồng (mặc định: 4)
    - `prompt_translation`: Prompt hướng dẫn phong cách dịch
    - `font_name`: Tên font chữ
    - `font_size_factor`: Hệ số cỡ chữ (mặc định: 1.0)

### 3. Kiểm tra trạng thái
- `GET /translate/{task_id}/status`: Kiểm tra trạng thái task

### 4. Tải xuống kết quả
- `GET /translate/{task_id}/download`: Tải xuống file PDF đã dịch
  - Query params:
    - `type`: 'mono' (chỉ văn bản đã dịch) hoặc 'dual' (song ngữ)

### 5. Danh sách dịch vụ
- `GET /services`: Lấy danh sách dịch vụ dịch hỗ trợ

### 6. Danh sách ngôn ngữ
- `GET /languages`: Lấy danh sách ngôn ngữ hỗ trợ

### 7. Danh sách font
- `GET /fonts`: Lấy danh sách font chữ hỗ trợ
  - Query params:
    - `language`: Mã ngôn ngữ để lọc font

### 8. Xóa task
- `DELETE /cleanup-task/{task_id}`: Xóa task và tài nguyên

### 9. Kiểm tra sức khỏe
- `GET /health`: Kiểm tra trạng thái API

### 10. Trích xuất văn bản
- `POST /extract-text`: Trích xuất văn bản và bounding boxes
  - Form-data:
    - `file`: File PDF cần trích xuất

## Ví dụ sử dụng

### Dịch PDF
```python
import requests

url = "http://localhost:7860/translate"
files = {"file": open("document.pdf", "rb")}
data = {
    "source_lang": "en",
    "target_lang": "vi",
    "service": "google",
    "font_name": "be_vietnam_pro"
}

response = requests.post(url, files=files, data=data)
task_id = response.json()["task_id"]

# Kiểm tra trạng thái
status = requests.get(f"http://localhost:7860/translate/{task_id}/status").json()

# Tải xuống kết quả
if status["status"] == "completed":
    result = requests.get(f"http://localhost:7860/translate/{task_id}/download")
    with open("translated.pdf", "wb") as f:
        f.write(result.content)
```

## Lưu ý

1. API sử dụng Google Translate mặc định, không cần API key
2. Các dịch vụ khác (DeepL, OpenAI, Gemini) cần API key
3. Kích thước file PDF tối đa: 20MB
4. Task sẽ tự động xóa sau 1 giờ
5. Font chữ mặc định cho tiếng Việt: Be Vietnam Pro 