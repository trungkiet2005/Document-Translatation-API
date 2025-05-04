from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import logging
import io
import threading
import time
import tempfile
import json
import numpy as np
from pdf2zh.doclayout import OnnxModel

# Cấu hình logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Thêm vào đầu file app.py, sau phần import và trước phần khởi tạo mô hình

# Đảm bảo thư mục cache tồn tại và có quyền ghi

# Đây phải là đoạn code đầu tiên sau các lệnh import cơ bản
import os
import sys

# Sửa các thư mục cache trước khi import pdf2zh
os.environ["XDG_CACHE_HOME"] = "/tmp/.cache"
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["HOME"] = "/tmp"  # Điều này sẽ khiến ~/.cache trỏ đến /tmp/.cache

# Tạo các thư mục cache với quyền ghi
os.makedirs("/tmp/.cache/huggingface", exist_ok=True)
os.makedirs("/tmp/.cache/pdf2zh", exist_ok=True)
os.makedirs("/tmp/pdf_translate_api", exist_ok=True)

# Khởi tạo mô hình DocLayout
# Trong app.py, thay đổi cách khởi tạo model
from pdf2zh.doclayout import ModelInstance, OnnxModel  # Ensure ModelInstance and OnnxModel are imported

try:
    if ModelInstance.value is None:
        ModelInstance.value = OnnxModel.load_available()
except Exception as e:
    logger.warning(f"Unable to load DocLayout model: {str(e)}")
    logger.warning("The application will still work but document layout analysis may be limited")
    # Không raise exception ở đây, để server vẫn có thể khởi động
    # nhưng API sẽ báo lỗi khi được gọi

# Khởi tạo Flask app
app = Flask(__name__)
CORS(app)  # Cho phép CORS để web frontend có thể gọi API

# Thư mục lưu trữ tạm thời
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "pdf_translate_api")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Lưu trữ status của các task
tasks = {}

@app.route('/', methods=['GET'])
def index():
    """
    Trang chính của API
    """
    return jsonify({
        'service': 'PDF Translation API',
        'version': '1.0.0',
        'status': 'running',
        'endpoints': {
            '/translate': 'POST - Dịch file PDF',
            '/translate/{task_id}/status': 'GET - Kiểm tra trạng thái',
            '/translate/{task_id}/download': 'GET - Tải xuống kết quả',
            '/services': 'GET - Danh sách dịch vụ dịch',
            '/languages': 'GET - Danh sách ngôn ngữ hỗ trợ',
            '/cleanup-task/{task_id}': 'DELETE - Xóa task',
            '/health': 'GET - Kiểm tra sức khỏe API',
            '/extract-text': 'POST - Trích xuất các đoạn văn bản với bounding boxes'
        }
    })

@app.route('/translate', methods=['POST'])
def translate_pdf():
    """
    API endpoint để dịch PDF
    
    Request:
    - Form-data với 'file': File PDF cần dịch
    - Các tham số tùy chọn:
        - source_lang: Ngôn ngữ nguồn (mặc định: 'en')
        - target_lang: Ngôn ngữ đích (mặc định: 'vi')
        - service: Dịch vụ dịch (mặc định: 'google')
        - threads: Số luồng (mặc định: 4)
        - prompt_translation: Prompt để hướng dẫn phong cách dịch (tùy chọn)
    
    Response:
    - JSON với task_id để theo dõi tiến trình
    """
    try:
        # Kiểm tra mô hình đã được tải chưa
        from pdf2zh.doclayout import ModelInstance
        if ModelInstance.value is None:
            return jsonify({
                'error': 'Mô hình DocLayout chưa được tải. Vui lòng thử lại sau.'
            }), 500
            
        # Kiểm tra file có được gửi không
        if 'file' not in request.files:
            return jsonify({'error': 'Không tìm thấy file trong request'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Không có file nào được chọn'}), 400
            
        # Đọc các tham số
        source_lang = request.form.get('source_lang', 'en')
        target_lang = request.form.get('target_lang', 'vi')
        service = request.form.get('service', 'google')
        prompt_translation = request.form.get('prompt_translation', '')
        try:
            threads = int(request.form.get('threads', 4))
            if threads < 1:
                threads = 1
            elif threads > 8:  # Giới hạn số luồng tối đa
                threads = 8
        except ValueError:
            threads = 4
        
        # Kiểm tra loại file
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Chỉ hỗ trợ file PDF'}), 400
        
        # Đọc file
        file_data = file.read()
        
        # Kiểm tra kích thước file (giới hạn 20MB)
        file_size_mb = len(file_data) / (1024 * 1024)
        if file_size_mb > 20:
            return jsonify({'error': 'Kích thước file vượt quá giới hạn 20MB'}), 400
        
        # Tạo task ID
        task_id = str(uuid.uuid4())
        
        # Lưu thông tin task
        tasks[task_id] = {
            'status': 'processing',
            'progress': 0,
            'filename': file.filename,
            'source_lang': source_lang,
            'target_lang': target_lang,
            'service': service,
            'prompt_translation': prompt_translation,
            'file_size': file_size_mb,
            'created_at': time.time()
        }
        
        # Chạy task xử lý file trong background
        thread = threading.Thread(
            target=process_task, 
            args=(task_id, file_data, source_lang, target_lang, service, threads, prompt_translation)
        )
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'task_id': task_id,
            'status': 'processing',
            'message': 'Đã bắt đầu xử lý file PDF'
        })
        
    except Exception as e:
        logger.exception("Lỗi khi xử lý yêu cầu dịch")
        return jsonify({'error': str(e)}), 500

def process_task(task_id, file_data, source_lang, target_lang, service, threads, prompt_translation=""):
    """Xử lý task dịch trong background"""
    try:
        # Import tại đây để tránh circular import
        from pdf2zh.high_level import translate_stream
        from pdf2zh.doclayout import ModelInstance
        
        # Kiểm tra mô hình đã được tải chưa
        if ModelInstance.value is None:
            if task_id in tasks:
                tasks[task_id].update({
                    'status': 'failed',
                    'error': 'Mô hình DocLayout chưa được tải',
                    'message': 'Lỗi khởi tạo mô hình DocLayout'
                })
            return
        
        # Cập nhật task progress callback
        def progress_callback(t):
            if hasattr(t, 'n') and hasattr(t, 'total'):
                progress = min(int((t.n / t.total) * 100), 99)  # Giới hạn ở 99% cho đến khi hoàn tất
                if task_id in tasks:  # Kiểm tra task còn tồn tại không
                    tasks[task_id]['progress'] = progress
                    logger.info(f"Task {task_id}: {progress}% complete")
        
        # Thực hiện dịch
        mono_data, dual_data = translate_stream(
            stream=file_data,
            lang_in=source_lang,
            lang_out=target_lang,
            service=service,
            thread=threads,
            callback=progress_callback,
            model=ModelInstance.value,  # Truyền rõ ràng model vào
            prompt=prompt_translation if prompt_translation else None
        )
        
        # Kiểm tra task còn tồn tại không
        if task_id in tasks:
            # Lưu kết quả vào task
            tasks[task_id].update({
                'status': 'completed',
                'progress': 100,
                'mono_data': mono_data,
                'dual_data': dual_data,
                'message': 'Dịch thành công',
                'completed_at': time.time()
            })
            
            logger.info(f"Task {task_id} đã hoàn tất")
            
            # Tự động xóa task sau 1 giờ
            cleanup_timer = threading.Timer(3600, cleanup_task_internal, args=[task_id])
            cleanup_timer.daemon = True
            cleanup_timer.start()
        
    except Exception as e:
        logger.exception(f"Lỗi xử lý task {task_id}")
        if task_id in tasks:
            tasks[task_id].update({
                'status': 'failed',
                'error': str(e),
                'message': 'Dịch thất bại: ' + str(e)
            })

def cleanup_task_internal(task_id):
    """Xóa task nội bộ sau thời gian chờ"""
    if task_id in tasks:
        logger.info(f"Tự động xóa task {task_id}")
        del tasks[task_id]

@app.route('/translate/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """
    Kiểm tra trạng thái của task dịch
    
    Response:
    - JSON với thông tin status và progress
    """
    if task_id not in tasks:
        return jsonify({'error': 'Không tìm thấy task'}), 404
        
    task = tasks[task_id]
    response = {
        'status': task['status'],
        'progress': task['progress'],
        'filename': task['filename'],
        'source_lang': task.get('source_lang'),
        'target_lang': task.get('target_lang'),
        'service': task.get('service')
    }
    
    if 'message' in task:
        response['message'] = task['message']
        
    if task['status'] == 'failed' and 'error' in task:
        response['error'] = task['error']
        
    return jsonify(response)

@app.route('/translate/<task_id>/download', methods=['GET'])
def download_result(task_id):
    """
    Tải xuống file PDF đã dịch
    
    Query parameters:
    - type: 'mono' (chỉ văn bản đã dịch) hoặc 'dual' (song ngữ, mặc định)
    
    Response:
    - File PDF đã dịch
    """
    if task_id not in tasks:
        return jsonify({'error': 'Không tìm thấy task'}), 404
        
    task = tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({
            'error': 'Task chưa hoàn tất', 
            'status': task['status'], 
            'progress': task['progress']
        }), 400
        
    result_type = request.args.get('type', 'dual')
    
    try:
        if result_type == 'mono':
            pdf_data = task['mono_data']
            filename = f"{os.path.splitext(task['filename'])[0]}_vi.pdf"
        else:  # 'dual'
            pdf_data = task['dual_data']
            filename = f"{os.path.splitext(task['filename'])[0]}_en_vi.pdf"
        
        return send_file(
            io.BytesIO(pdf_data),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception(f"Lỗi khi tải xuống file cho task {task_id}")
        return jsonify({'error': f'Lỗi khi tải xuống file: {str(e)}'}), 500

@app.route('/services', methods=['GET'])
def get_available_services():
    """
    Lấy danh sách các dịch vụ dịch thuật hỗ trợ
    
    Response:
    - JSON với danh sách dịch vụ
    """
    services = [
        {"id": "google", "name": "Google Translate", "description": "Dịch vụ Google Translate (mặc định, miễn phí)"},
        {"id": "bing", "name": "Bing Translate", "description": "Dịch vụ Bing Translate (miễn phí)"},
        {"id": "deepl", "name": "DeepL", "description": "Dịch vụ DeepL (yêu cầu API key)"},
        {"id": "openai", "name": "OpenAI", "description": "Dịch thuật bằng OpenAI (yêu cầu API key)"},
        {"id": "gemini", "name": "Google Gemini", "description": "Dịch thuật bằng Google Gemini (yêu cầu API key)"}
    ]
    return jsonify(services)

@app.route('/languages', methods=['GET'])
def get_available_languages():
    """
    Lấy danh sách các ngôn ngữ hỗ trợ
    
    Response:
    - JSON với danh sách ngôn ngữ nguồn và đích
    """
    languages = {
        "source": [
            {"code": "en", "name": "Tiếng Anh", "default": True},
            {"code": "fr", "name": "Tiếng Pháp"},
            {"code": "de", "name": "Tiếng Đức"},
            {"code": "ja", "name": "Tiếng Nhật"},
            {"code": "ko", "name": "Tiếng Hàn"},
            {"code": "ru", "name": "Tiếng Nga"},
            {"code": "es", "name": "Tiếng Tây Ban Nha"},
            {"code": "it", "name": "Tiếng Ý"},
            {"code": "zh", "name": "Tiếng Trung (Giản thể)"},
            {"code": "zh-TW", "name": "Tiếng Trung (Phồn thể)"}
        ],
        "target": [
            {"code": "vi", "name": "Tiếng Việt", "default": True},
            {"code": "en", "name": "Tiếng Anh"},
            {"code": "fr", "name": "Tiếng Pháp"},
            {"code": "de", "name": "Tiếng Đức"},
            {"code": "ja", "name": "Tiếng Nhật"},
            {"code": "ko", "name": "Tiếng Hàn"},
            {"code": "ru", "name": "Tiếng Nga"},
            {"code": "es", "name": "Tiếng Tây Ban Nha"},
            {"code": "it", "name": "Tiếng Ý"},
            {"code": "zh", "name": "Tiếng Trung (Giản thể)"},
            {"code": "zh-TW", "name": "Tiếng Trung (Phồn thể)"}
        ]
    }
    return jsonify(languages)

@app.route('/cleanup-task/<task_id>', methods=['DELETE'])
def cleanup_task(task_id):
    """
    Xóa task và tài nguyên liên quan
    
    Response:
    - JSON với kết quả xóa
    """
    if task_id in tasks:
        del tasks[task_id]
        return jsonify({'status': 'success', 'message': 'Đã xóa task thành công'})
    else:
        return jsonify({'error': 'Không tìm thấy task'}), 404

@app.route('/health', methods=['GET'])
def health_check():
    """
    Kiểm tra trạng thái của API
    
    Response:
    - JSON với trạng thái api
    """
    from pdf2zh.doclayout import ModelInstance
    
    model_status = "loaded" if ModelInstance.value is not None else "not_loaded"
    
    return jsonify({
        'status': 'ok', 
        'version': '1.0.0',
        'service': 'PDF Translation API',
        'active_tasks': len(tasks),
        'model_status': model_status
    })

@app.route('/extract-text', methods=['POST'])
def extract_text_chunks():
    """
    Trích xuất các đoạn văn bản với bounding boxes từ file PDF
    
    Request:
    - Form-data với 'file': File PDF cần trích xuất
    
    Response:
    - JSON với danh sách các đoạn văn bản và bounding boxes
    """
    try:
        # Kiểm tra file có được gửi không
        if 'file' not in request.files:
            return jsonify({'error': 'Không tìm thấy file trong request'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Không có file nào được chọn'}), 400
        
        # Kiểm tra loại file
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Chỉ hỗ trợ file PDF'}), 400
        
        # Đọc file
        file_data = file.read()
        
        # Kiểm tra kích thước file (giới hạn 20MB)
        file_size_mb = len(file_data) / (1024 * 1024)
        if file_size_mb > 20:
            return jsonify({'error': 'Kích thước file vượt quá giới hạn 20MB'}), 400
        
        # Trích xuất văn bản và bounding boxes
        from pdf2zh.doclayout import ModelInstance
        if ModelInstance.value is None:
            return jsonify({
                'error': 'Mô hình DocLayout chưa được tải. Vui lòng thử lại sau.'
            }), 500
        
        try:
            text_chunks = ModelInstance.value.extract_text_chunks(file_data)
            return jsonify({'text_chunks': text_chunks})
        except Exception as e:
            logger.exception("Lỗi khi trích xuất văn bản")
            return jsonify({'error': str(e)}), 500
        
    except Exception as e:
        logger.exception("Lỗi khi xử lý yêu cầu trích xuất văn bản")
        return jsonify({'error': str(e)}), 500

# Dọn dẹp file tạm định kỳ (chạy trong thread riêng)
def periodic_cleanup():
    """Dọn dẹp task cũ và file tạm thời"""
    logger.info("Bắt đầu dọn dẹp định kỳ")
    # Xóa các task quá 24 giờ
    current_tasks = list(tasks.keys())
    for task_id in current_tasks:
        if task_id in tasks and tasks[task_id].get('created_at', 0) < time.time() - 86400:
            logger.info(f"Xóa task cũ {task_id}")
            del tasks[task_id]
    
    # Lên lịch chạy lại sau 1 giờ
    cleanup_timer = threading.Timer(3600, periodic_cleanup)
    cleanup_timer.daemon = True
    cleanup_timer.start()

# Khởi tạo dọn dẹp định kỳ
def start_cleanup_thread():
    cleanup_thread = threading.Thread(target=periodic_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()

# Khởi tạo khi server bắt đầu
if __name__ == '__main__':
    # Bắt đầu dọn dẹp định kỳ
    start_cleanup_thread()
    
    # Lấy cổng từ biến môi trường hoặc sử dụng cổng 7860 (mặc định cho Hugging Face)
    port = int(os.environ.get("PORT", 7860))
    
    # Sử dụng Flask development server cho môi trường phát triển
    # Trong môi trường sản xuất, Hugging Face sẽ sử dụng gunicorn
    app.run(host="0.0.0.0", port=port)