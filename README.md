# PDF Translate API

## Giới thiệu

Dự án này cung cấp một API để dịch tài liệu PDF, đặc biệt tập trung vào việc dịch các tài liệu chứa công thức toán học. API có khả năng duy trì cấu trúc, định dạng và bố cục của tài liệu gốc trong quá trình dịch.

## Tính năng chính

- Dịch nội dung tài liệu PDF với sự hỗ trợ nhiều ngôn ngữ
- Bảo toàn công thức toán học trong quá trình dịch
- Duy trì bố cục và định dạng của tài liệu gốc
- Hỗ trợ đa dạng font chữ, bao gồm font tiếng Việt
- Xử lý bất đồng bộ cho các tài liệu dài
- API RESTful cho phép tích hợp dễ dàng

## Yêu cầu hệ thống

- Python 3.8+
- Các thư viện được liệt kê trong file `requirements.txt`

## Cài đặt

### Sử dụng Docker

```bash
# Tạo Docker image
docker build -t pdf-math-translate .

# Chạy container
docker run -p 7860:7860 pdf-math-translate
```

### Cài đặt thủ công

```bash
# Tạo môi trường ảo (tùy chọn)
python -m venv pdftranslate-env
source pdftranslate-env/bin/activate  # Linux/Mac
pdftranslate-env\Scripts\activate  # Windows

# Cài đặt các phụ thuộc
pip install -r requirements.txt

# Chạy ứng dụng
python app.py
```

## Sử dụng API

### Các endpoint chính

- `POST /translate`: Dịch tài liệu PDF
- `GET /translate/{task_id}/status`: Kiểm tra trạng thái tiến trình
- `GET /translate/{task_id}/download`: Tải xuống tài liệu đã dịch
- `GET /services`: Danh sách dịch vụ dịch thuật
- `GET /languages`: Danh sách ngôn ngữ hỗ trợ
- `GET /fonts`: Danh sách font chữ hỗ trợ
- `POST /extract-text`: Trích xuất văn bản với vị trí bounding box

### Ví dụ

```python
import requests

# Dịch PDF
files = {'file': open('tài_liệu.pdf', 'rb')}
data = {
    'source_language': 'en',
    'target_language': 'vi',
    'service': 'google'
}
response = requests.post('http://localhost:7860/translate', files=files, data=data)
task_id = response.json()['task_id']

# Kiểm tra trạng thái
status_response = requests.get(f'http://localhost:7860/translate/{task_id}/status')
print(status_response.json())

# Tải xuống kết quả khi hoàn thành
if status_response.json()['status'] == 'completed':
    download_response = requests.get(f'http://localhost:7860/translate/{task_id}/download')
    with open('tài_liệu_đã_dịch.pdf', 'wb') as f:
        f.write(download_response.content)
```

## Tùy chỉnh font chữ

API này hỗ trợ nhiều font chữ, bao gồm các font tiếng Việt như:
- Noto Sans Vietnamese (mặc định)
- Be Vietnam Pro
- SVN-Gilroy
- SVN-Poppins
- Và nhiều font khác...

## Đóng góp

Chúng tôi hoan nghênh mọi đóng góp cho dự án! Vui lòng tạo pull request hoặc báo cáo lỗi.

## Giấy phép

Xem file [LICENSE](LICENSE) để biết thêm thông tin.
