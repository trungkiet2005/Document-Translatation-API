from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import uuid
import logging
import json
import io
from pdf2zh import translate_stream
import tempfile
import asyncio

# Cấu hình logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Khởi tạo Flask app
app = Flask(__name__)
CORS(app)  # Cho phép CORS để web frontend có thể gọi API

# Thư mục lưu trữ tạm thời
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "pdf_translate_api")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Lưu trữ status của các task
tasks = {}

@app.route('/api/v1/translate', methods=['POST'])
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
    
    Response:
    - JSON với task_id để theo dõi tiến trình
    """
    try:
        # Kiểm tra file có được gửi không
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No selected file'}), 400
            
        # Đọc các tham số
        source_lang = request.form.get('source_lang', 'en')
        target_lang = request.form.get('target_lang', 'vi')
        service = request.form.get('service', 'google')
        threads = int(request.form.get('threads', 4))
        
        # Kiểm tra loại file
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Only PDF files are allowed'}), 400
        
        # Đọc file
        file_data = file.read()
        
        # Tạo task ID
        task_id = str(uuid.uuid4())
        
        # Lưu thông tin task
        tasks[task_id] = {
            'status': 'processing',
            'progress': 0,
            'filename': file.filename
        }
        
        # Chạy task xử lý file trong background
        process_task(task_id, file_data, source_lang, target_lang, service, threads)
        
        return jsonify({
            'task_id': task_id,
            'status': 'processing'
        })
        
    except Exception as e:
        logger.exception("Error processing translation request")
        return jsonify({'error': str(e)}), 500

def process_task(task_id, file_data, source_lang, target_lang, service, threads):
    """Xử lý task dịch trong background"""
    try:
        # Cập nhật task progress callback
        def progress_callback(t):
            if hasattr(t, 'n') and hasattr(t, 'total'):
                progress = int((t.n / t.total) * 100)
                tasks[task_id]['progress'] = progress
                logger.info(f"Task {task_id}: {progress}% complete")
        
        # Thực hiện dịch
        mono_data, dual_data = translate_stream(
            stream=file_data,
            lang_in=source_lang,
            lang_out=target_lang,
            service=service,
            thread=threads,
            callback=progress_callback
        )
        
        # Lưu kết quả vào task
        tasks[task_id].update({
            'status': 'completed',
            'progress': 100,
            'mono_data': mono_data,
            'dual_data': dual_data
        })
        
        logger.info(f"Task {task_id} completed successfully")
        
    except Exception as e:
        logger.exception(f"Error processing task {task_id}")
        tasks[task_id].update({
            'status': 'failed',
            'error': str(e)
        })

@app.route('/api/v1/translate/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """
    Kiểm tra trạng thái của task dịch
    
    Response:
    - JSON với thông tin status và progress
    """
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404
        
    task = tasks[task_id]
    response = {
        'status': task['status'],
        'progress': task['progress']
    }
    
    if task['status'] == 'failed' and 'error' in task:
        response['error'] = task['error']
        
    return jsonify(response)

@app.route('/api/v1/translate/<task_id>/download', methods=['GET'])
def download_result(task_id):
    """
    Tải xuống file PDF đã dịch
    
    Query parameters:
    - type: 'mono' (chỉ văn bản đã dịch) hoặc 'dual' (song ngữ, mặc định)
    
    Response:
    - File PDF đã dịch
    """
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404
        
    task = tasks[task_id]
    if task['status'] != 'completed':
        return jsonify({'error': 'Task not completed yet'}), 400
        
    result_type = request.args.get('type', 'dual')
    
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

@app.route('/api/v1/services', methods=['GET'])
def get_available_services():
    """
    Lấy danh sách các dịch vụ dịch thuật hỗ trợ
    
    Response:
    - JSON với danh sách dịch vụ
    """
    services = [
        {"id": "google", "name": "Google Translate", "description": "Dịch vụ Google Translate (mặc định)"},
        {"id": "bing", "name": "Bing Translate", "description": "Dịch vụ Bing Translate"},
        {"id": "deepl", "name": "DeepL", "description": "Dịch vụ DeepL (yêu cầu API key)"},
        {"id": "openai", "name": "OpenAI", "description": "Dịch thuật bằng OpenAI (yêu cầu API key)"}
    ]
    return jsonify(services)

@app.route('/api/v1/languages', methods=['GET'])
def get_available_languages():
    """
    Lấy danh sách các ngôn ngữ hỗ trợ
    
    Response:
    - JSON với danh sách ngôn ngữ nguồn và đích
    """
    languages = {
        "source": [
            {"code": "en", "name": "English", "default": True},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "ja", "name": "Japanese"},
            {"code": "ko", "name": "Korean"},
            {"code": "ru", "name": "Russian"},
            {"code": "es", "name": "Spanish"},
            {"code": "it", "name": "Italian"},
            {"code": "zh", "name": "Chinese (Simplified)"},
            {"code": "zh-TW", "name": "Chinese (Traditional)"}
        ],
        "target": [
            {"code": "vi", "name": "Vietnamese", "default": True},
            {"code": "en", "name": "English"},
            {"code": "fr", "name": "French"},
            {"code": "de", "name": "German"},
            {"code": "ja", "name": "Japanese"},
            {"code": "ko", "name": "Korean"},
            {"code": "ru", "name": "Russian"},
            {"code": "es", "name": "Spanish"},
            {"code": "it", "name": "Italian"},
            {"code": "zh", "name": "Chinese (Simplified)"},
            {"code": "zh-TW", "name": "Chinese (Traditional)"}
        ]
    }
    return jsonify(languages)

@app.route('/api/v1/cleanup-task/<task_id>', methods=['DELETE'])
def cleanup_task(task_id):
    """
    Xóa task và tài nguyên liên quan
    
    Response:
    - JSON với kết quả xóa
    """
    if task_id in tasks:
        del tasks[task_id]
        return jsonify({'status': 'success', 'message': 'Task cleaned up'})
    else:
        return jsonify({'error': 'Task not found'}), 404

@app.route('/api/v1/health', methods=['GET'])
def health_check():
    """
    Kiểm tra trạng thái của API
    
    Response:
    - JSON với trạng thái api
    """
    return jsonify({
        'status': 'ok', 
        'version': '1.0.0',
        'service': 'PDF Translation API'
    })

# Dọn dẹp file tạm định kỳ (chạy trong thread riêng)
def cleanup_temp_files():
    # Triển khai dọn dẹp file tạm thời nếu cần
    pass

if __name__ == '__main__':
    # Đặt host='0.0.0.0' để service có thể được truy cập từ các máy khác
    app.run(host='0.0.0.0', port=5000, debug=True)