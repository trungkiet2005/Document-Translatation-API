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
from code_pdf.doclayout import OnnxModel
import re
import requests
from pathlib import Path

# Cấu hình logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Đảm bảo thư mục cache tồn tại và có quyền ghi
import os
import sys

# Sửa các thư mục cache trước khi import code_pdf
os.environ["XDG_CACHE_HOME"] = "/tmp/.cache"
os.environ["HF_HOME"] = "/tmp/.cache/huggingface"
os.environ["HOME"] = "/tmp"  # Điều này sẽ khiến ~/.cache trỏ đến /tmp/.cache

# Tạo các thư mục cache với quyền ghi
os.makedirs("/tmp/.cache/huggingface", exist_ok=True)
os.makedirs("/tmp/.cache/code_pdf", exist_ok=True)
os.makedirs("/tmp/pdf_translate_api", exist_ok=True)

# Khởi tạo mô hình DocLayout
from code_pdf.doclayout import ModelInstance, OnnxModel

# Đường dẫn đến font Noto Sans Vietnamese mặc định
NOTO_SANS_VIETNAMESE_URL = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSansVietnamese-Regular.ttf"

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

# Thư mục font
FONT_FOLDER = os.path.join(os.environ.get("XDG_CACHE_HOME", "/tmp/.cache"), "babeldoc", "fonts")
os.makedirs(FONT_FOLDER, exist_ok=True)

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
            '/fonts': 'GET - Danh sách font chữ hỗ trợ',
            '/cleanup-task/{task_id}': 'DELETE - Xóa task',
            '/health': 'GET - Kiểm tra sức khỏe API',
            '/extract-text': 'POST - Trích xuất các đoạn văn bản với bounding boxes'
        }
    })

# Hàm tiện ích để quét font hệ thống
def scan_system_fonts():
    """Quét và trả về danh sách font hệ thống"""
    system_fonts = []
    
    # Windows font directories
    font_dirs = [
        os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft\\Windows\\Fonts"),
        os.path.join(os.path.expanduser("~"), "AppData\\Local\\Microsoft\\Windows\\Fonts"),
    ]
    
    # Thêm thư mục font cache của ứng dụng
    font_dirs.append(FONT_FOLDER)
    
    # Các phần mở rộng font hợp lệ
    valid_extensions = ['.ttf', '.otf', '.TTF', '.OTF']
    
    # Quét các thư mục font
    for font_dir in font_dirs:
        if os.path.exists(font_dir):
            try:
                for file in os.listdir(font_dir):
                    if any(file.endswith(ext) for ext in valid_extensions):
                        font_name = os.path.splitext(file)[0]
                        # Chuyển đổi tên font để sử dụng trong API
                        safe_font_name = font_name.replace(" ", "_")
                        font_path = os.path.join(font_dir, file)
                        font_info = {
                            "id": safe_font_name,
                            "name": font_name,
                            "path": font_path
                        }
                        
                        # Kiểm tra trùng lặp trước khi thêm vào
                        if not any(f["id"] == safe_font_name for f in system_fonts):
                            system_fonts.append(font_info)
            except Exception as e:
                logger.warning(f"Lỗi khi quét thư mục font {font_dir}: {str(e)}")
    
    # Thêm các font phổ biến được tích hợp sẵn
    common_fonts = [
        {"id": "noto_sans_vietnamese", "name": "Noto Sans Vietnamese", "type": "sans-serif", "for_language": "vi", "default": True},
        {"id": "roboto", "name": "Roboto", "type": "sans-serif"},
        {"id": "arial", "name": "Arial", "type": "sans-serif"},
        {"id": "times", "name": "Times New Roman", "type": "serif"},
        {"id": "verdana", "name": "Verdana", "type": "sans-serif"},
        {"id": "source_han_serif", "name": "Source Han Serif", "type": "serif", "for_language": "zh,ja,ko"},
        # Thêm các font tiếng Việt phổ biến
        {"id": "be_vietnam_pro", "name": "Be Vietnam Pro", "type": "sans-serif", "for_language": "vi"},
        {"id": "roboto_condensed", "name": "Roboto Condensed", "type": "sans-serif", "for_language": "vi"},
        {"id": "open_sans", "name": "Open Sans", "type": "sans-serif", "for_language": "vi"},
        {"id": "montserrat", "name": "Montserrat", "type": "sans-serif", "for_language": "vi"},
        # Thêm font tiếng Việt tốt hơn
        {"id": "svn_gilroy", "name": "SVN-Gilroy", "type": "sans-serif", "for_language": "vi"},
        {"id": "nunito", "name": "Nunito", "type": "sans-serif", "for_language": "vi"},
        {"id": "svn_source_sans_3", "name": "SVN-Source Sans 3", "type": "sans-serif", "for_language": "vi"},
        {"id": "svn_poppins", "name": "SVN-Poppins", "type": "sans-serif", "for_language": "vi"},
        {"id": "inter", "name": "Inter", "type": "sans-serif", "for_language": "vi"},
        {"id": "lexend", "name": "Lexend", "type": "sans-serif", "for_language": "vi"},
        {"id": "svn_gotham", "name": "SVN-Gotham", "type": "sans-serif", "for_language": "vi"},
        {"id": "mulish", "name": "Mulish", "type": "sans-serif", "for_language": "vi"},
        {"id": "san_francisco", "name": "San Francisco", "type": "sans-serif", "for_language": "vi"},
    ]
    
    for common_font in common_fonts:
        if not any(f["id"] == common_font["id"] for f in system_fonts):
            system_fonts.append(common_font)
    
    return system_fonts

@app.route('/fonts', methods=['GET'])
def get_available_fonts():
    """
    Lấy danh sách font chữ hỗ trợ
    
    Query parameters:
    - language: Mã ngôn ngữ để lọc font phù hợp (tùy chọn)
    
    Response:
    - JSON với danh sách font hỗ trợ
    """
    try:
        # Lọc theo ngôn ngữ nếu được chỉ định
        language = request.args.get('language', '').lower()
        
        # Quét font hệ thống
        system_fonts = scan_system_fonts()
        
        # Font mặc định theo ngôn ngữ
        default_fonts = {
            "vi": "noto_sans_vietnamese",  # Sử dụng Noto Sans Vietnamese làm font mặc định cho tiếng Việt
            "en": "arial",
            "zh": "source_han_serif",
            "ja": "source_han_serif",
            "ko": "source_han_serif",
        }
        
        # Tổ chức lại kết quả
        fonts = []
        for font in system_fonts:
            font_entry = {
                "id": font["id"],
                "name": font.get("name", font["id"])
            }
            
            # Thêm thông tin kiểu font nếu có
            if "type" in font:
                font_entry["type"] = font["type"]
                
            # Đánh dấu font mặc định theo ngôn ngữ
            if language and language in default_fonts and default_fonts[language] == font["id"]:
                font_entry["default"] = True
                
            # Nếu đang lọc theo ngôn ngữ và font này không hỗ trợ ngôn ngữ đó, bỏ qua
            if language and "for_language" in font and language not in font["for_language"].split(','):
                continue
                
            fonts.append(font_entry)
        
        # Sắp xếp font, đưa font mặc định lên đầu
        fonts.sort(key=lambda x: (0 if x.get("default", False) else 1, x["name"]))
        
        return jsonify(fonts)
    
    except Exception as e:
        logger.exception("Lỗi khi lấy danh sách font")
        return jsonify({'error': str(e)}), 500

# Validate font name để đảm bảo an toàn
def validate_font_name(font_name):
    """Kiểm tra và chuẩn hóa tên font để đảm bảo an toàn"""
    if not font_name:
        return ""
        
    # Loại bỏ ký tự đặc biệt và dấu cách
    safe_font_name = re.sub(r'[^a-zA-Z0-9_-]', '', font_name.replace(" ", "_"))
    return safe_font_name

# Validate font size factor
def validate_font_size_factor(factor_str):
    """Kiểm tra và chuẩn hóa hệ số cỡ chữ"""
    try:
        factor = float(factor_str)
        # Giới hạn trong khoảng hợp lý
        if factor < 0.5:
            return 0.5
        elif factor > 2.0:
            return 2.0
        return factor
    except (ValueError, TypeError):
        return 1.0  # Giá trị mặc định

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
        - font_name: Tên font chữ cho văn bản đã dịch (tùy chọn)
        - font_size_factor: Hệ số điều chỉnh cỡ chữ (mặc định: 1.0)
        - letter_spacing: Khoảng cách giữa các ký tự, hữu ích cho tiếng Việt (mặc định: 0.02)
    
    Response:
    - JSON với task_id để theo dõi tiến trình
    """
    try:
        # Kiểm tra mô hình đã được tải chưa
        from code_pdf.doclayout import ModelInstance
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
        
        # Xử lý font name một cách an toàn
        font_name = validate_font_name(request.form.get('font_name', ''))
        
        # Xử lý font size factor
        font_size_factor = validate_font_size_factor(request.form.get('font_size_factor', 1.0))
        
        # Xử lý letter spacing (hữu ích cho tiếng Việt)
        try:
            letter_spacing = float(request.form.get('letter_spacing', 0.02))
            # Giới hạn trong khoảng hợp lý
            if letter_spacing < 0:
                letter_spacing = 0
            elif letter_spacing > 0.1:
                letter_spacing = 0.1
        except (ValueError, TypeError):
            letter_spacing = 0.02  # Giá trị mặc định
        
        # Xử lý các tùy chọn nâng cao cho tiếng Việt
        use_accent_positioning = request.form.get('use_accent_positioning', 'true').lower() == 'true'
        use_font_substitution = request.form.get('use_font_substitution', 'true').lower() == 'true'
        use_line_height_adjustment = request.form.get('use_line_height_adjustment', 'true').lower() == 'true'
        
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
            'font_name': font_name,
            'font_size_factor': font_size_factor,
            'letter_spacing': letter_spacing,
            'use_accent_positioning': use_accent_positioning,
            'use_font_substitution': use_font_substitution,
            'use_line_height_adjustment': use_line_height_adjustment,
            'file_size': file_size_mb,
            'created_at': time.time()
        }
        
        # Chạy task xử lý file trong background
        thread = threading.Thread(
            target=process_task, 
            args=(task_id, file_data, source_lang, target_lang, service, threads, 
                  prompt_translation, font_name, font_size_factor, letter_spacing,
                  use_accent_positioning, use_font_substitution, use_line_height_adjustment)
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

# Hàm kiểm tra và tải font Noto Sans Vietnamese nếu cần
def ensure_vietnamese_font_available():
    """Kiểm tra và tải font Noto Sans Vietnamese nếu chưa có."""
    try:
        # Tạo thư mục font cache nếu chưa có
        font_cache_dir = Path(FONT_FOLDER)
        font_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Đường dẫn đến font Noto Sans Vietnamese
        noto_viet_path = font_cache_dir / "NotoSansVietnamese-Regular.ttf"
        
        # Kiểm tra xem font đã tồn tại chưa
        if not noto_viet_path.exists():
            logger.info("Không tìm thấy font Noto Sans Vietnamese, đang tải xuống...")
            
            # Tải font từ URL đã định nghĩa
            response = requests.get(NOTO_SANS_VIETNAMESE_URL, stream=True)
            if response.status_code == 200:
                with open(noto_viet_path, 'wb') as f:
                    f.write(response.content)
                logger.info(f"Đã tải xuống font Noto Sans Vietnamese thành công: {noto_viet_path}")
                return str(noto_viet_path)
            else:
                logger.warning(f"Không thể tải font Noto Sans Vietnamese: {response.status_code}")
                return None
        else:
            logger.info(f"Font Noto Sans Vietnamese đã tồn tại: {noto_viet_path}")
            return str(noto_viet_path)
    except Exception as e:
        logger.exception(f"Lỗi khi tải font Noto Sans Vietnamese: {str(e)}")
        return None

# Hàm kiểm tra font có hỗ trợ tiếng Việt đầy đủ không
def check_vietnamese_support(font_path):
    """Kiểm tra xem font có hỗ trợ đầy đủ các ký tự tiếng Việt không."""
    try:
        # Danh sách các ký tự đặc trưng cần kiểm tra
        vietnamese_chars = "áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ"
        vietnamese_chars += vietnamese_chars.upper()
        
        # Kiểm tra xem font có hỗ trợ TrueType/OpenType không
        if not font_path or not os.path.exists(font_path):
            return False
        
        # Thử đọc font để kiểm tra
        try:
            from fontTools import ttLib
            font = ttLib.TTFont(font_path)
            
            # Kiểm tra bảng cmap
            cmap = font.getBestCmap()
            
            # Kiểm tra các ký tự tiếng Việt
            for char in vietnamese_chars:
                if ord(char) not in cmap:
                    return False
                    
            return True
        except:
            # Nếu không thể kiểm tra với fontTools, thử phương pháp khác
            return True  # Tạm giả định là hỗ trợ
            
    except Exception as e:
        logger.exception(f"Lỗi khi kiểm tra font hỗ trợ tiếng Việt: {str(e)}")
        return False

def process_task(task_id, file_data, source_lang, target_lang, service, threads, 
                prompt_translation="", font_name="", font_size_factor=1.0, letter_spacing=0.02,
                use_accent_positioning=True, use_font_substitution=True, use_line_height_adjustment=True):
    """Xử lý task dịch trong background"""
    try:
        # Import tại đây để tránh circular import
        from code_pdf.high_level import translate_stream
        from code_pdf.doclayout import ModelInstance
        from string import Template
        import os
        
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
        
        # Chuẩn bị prompt nếu có
        prompt_template = None
        if prompt_translation:
            prompt_template = Template(prompt_translation)
        
        # Kiểm tra và xác định font_name
        system_fonts = scan_system_fonts()
        selected_font = None
        
        # Cấu hình font đặc biệt cho tiếng Việt
        vi_font_config = {}
        if target_lang == 'vi':
            # Đảm bảo có font Noto Sans Vietnamese
            noto_font_path = ensure_vietnamese_font_available()
            
            # Tăng letterSpacing để hiển thị dấu tiếng Việt tốt hơn
            vi_font_config = {
                'letterSpacing': letter_spacing,
                'wordSpacing': 0.1,     # Khoảng cách giữa các từ
                'ascenderMultiplier': 1.2,  # Hệ số cho phần trên của chữ
                'lineHeight': 1.4 if use_line_height_adjustment else 1.2,  # Tăng chiều cao dòng cho diacritical marks
                'forceSubset': False,   # Tránh subsetting font để giữ đầy đủ các ký tự
                'fallbackFonts': ['Noto Sans Vietnamese', 'Arial Unicode MS', 'Times New Roman'],
                'verticalOffset': 0.05, # Điều chỉnh offset dọc cho dấu tiếng Việt
                'embedFullFont': True,  # Nhúng toàn bộ font vào PDF
                'optimizeVietnamese': True,  # Tối ưu hóa cụ thể cho tiếng Việt
                'enableAccentPositioning': use_accent_positioning,  # Định vị dấu tiếng Việt
                'useFontSubstitution': use_font_substitution,  # Sử dụng thay thế font cho các ký tự đặc biệt
                'vietnameseCharMapping': {
                    # Mapping đặc biệt cho các ký tự tiếng Việt khó xử lý
                    'ă': {'verticalOffset': 0.05},
                    'Ă': {'verticalOffset': 0.05},
                    'â': {'verticalOffset': 0.05},
                    'Â': {'verticalOffset': 0.05},
                    'đ': {'letterSpacing': 0.03},
                    'Đ': {'letterSpacing': 0.03},
                    'ư': {'letterSpacing': 0.03},
                    'Ư': {'letterSpacing': 0.03},
                    'ơ': {'letterSpacing': 0.03},
                    'Ơ': {'letterSpacing': 0.03}
                }
            }
            
            # Tạo mapping font thay thế
            if use_font_substitution:
                # Ma trận thay thế font cho các ký tự đặc biệt
                font_substitution_matrix = {
                    'A': {'Ă': 'Noto Sans Vietnamese', 'Â': 'Noto Sans Vietnamese'},
                    'a': {'ă': 'Noto Sans Vietnamese', 'â': 'Noto Sans Vietnamese'},
                    'E': {'Ê': 'Noto Sans Vietnamese'},
                    'e': {'ê': 'Noto Sans Vietnamese'},
                    'O': {'Ô': 'Noto Sans Vietnamese', 'Ơ': 'Noto Sans Vietnamese'},
                    'o': {'ô': 'Noto Sans Vietnamese', 'ơ': 'Noto Sans Vietnamese'},
                    'U': {'Ư': 'Noto Sans Vietnamese'},
                    'u': {'ư': 'Noto Sans Vietnamese'},
                    'D': {'Đ': 'Noto Sans Vietnamese'},
                    'd': {'đ': 'Noto Sans Vietnamese'}
                }
                
                vi_font_config['fontSubstitutionMatrix'] = font_substitution_matrix
            
            # Nếu đã tải được font Noto, thêm vào danh sách fonts
            if noto_font_path:
                vi_font_config['notoFontPath'] = noto_font_path
                vi_font_config['primaryFallbackFont'] = noto_font_path
            
            # Sử dụng Noto Sans Vietnamese làm font mặc định cho tiếng Việt
            if not font_name:
                # Đảm bảo có Noto Sans Vietnamese
                noto_font_path = ensure_vietnamese_font_available()
                
                # Mặc định sử dụng Noto Sans Vietnamese
                font_name = "noto_sans_vietnamese"
                
                # Nếu có đường dẫn font, thiết lập override
                if noto_font_path:
                    vi_font_config['overrideFont'] = noto_font_path
        
        if font_name:
            # Tìm font trong danh sách
            for font in system_fonts:
                if font["id"].lower() == font_name.lower():
                    selected_font = font
                    font_name = font["id"]  # Sử dụng ID font đã chuẩn hóa
                    logger.info(f"Đã tìm thấy font: {font['name']}")
                    
                    # Kiểm tra hỗ trợ tiếng Việt nếu target_lang là 'vi'
                    if target_lang == 'vi' and "path" in font:
                        font_supports_vietnamese = check_vietnamese_support(font["path"])
                        if not font_supports_vietnamese:
                            logger.warning(f"Font {font['name']} không hỗ trợ đầy đủ tiếng Việt, đang chuyển sang font dự phòng")
                            # Dùng Noto Sans Vietnamese làm font dự phòng
                            if noto_font_path:
                                font_name = "Noto Sans Vietnamese"
                                vi_font_config['overrideFont'] = noto_font_path
                    
                    break
                    
            if not selected_font:
                logger.warning(f"Không tìm thấy font '{font_name}' trong hệ thống, sẽ sử dụng font mặc định")
                font_name = ""
                
        # Log thông tin font và cỡ chữ
        logger.info(f"Sử dụng font '{font_name}' với hệ số cỡ chữ {font_size_factor}")
                
        # Thực hiện dịch
        mono_data, dual_data = translate_stream(
            stream=file_data,
            lang_in=source_lang,
            lang_out=target_lang,
            service=service,
            thread=threads,
            callback=progress_callback,
            model=ModelInstance.value,
            prompt=prompt_template,
            font_name=font_name,
            font_size_factor=font_size_factor,
            **vi_font_config  # Thêm cấu hình font cho tiếng Việt
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
        'service': task.get('service'),
        'font_name': task.get('font_name', ''),
        'font_size_factor': task.get('font_size_factor', 1.0),
        'letter_spacing': task.get('letter_spacing', 0.02)
    }
    
    # Thêm thông tin cấu hình tiếng Việt nếu có
    if task.get('target_lang') == 'vi':
        response.update({
            'vietnamese_optimizations': {
                'use_accent_positioning': task.get('use_accent_positioning', True),
                'use_font_substitution': task.get('use_font_substitution', True),
                'use_line_height_adjustment': task.get('use_line_height_adjustment', True)
            }
        })
    
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
    from code_pdf.doclayout import ModelInstance
    
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
        from code_pdf.doclayout import ModelInstance
        if ModelInstance.value is None:
            return jsonify({
                'error': 'Mô hình DocLayout chưa được tải. Vui lòng thử lại sau.'
            }), 500
        
        try:
            # Đảm bảo các thư viện phụ thuộc được import
            try:
                import pymupdf
            except ImportError:
                # Nếu pymupdf chưa được cài đặt, thử cài đặt
                logger.warning("Thư viện pymupdf chưa được cài đặt, đang thử cài đặt...")
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "pymupdf"])
                import pymupdf
            
            try:
                from pdfminer.pdfparser import PDFParser
                from pdfminer.pdfdocument import PDFDocument
                from pdfminer.pdfpage import PDFPage
                from pdfminer.pdfinterp import PDFResourceManager
                from pdfminer.pdfinterp import PDFPageInterpreter
                from pdfminer.layout import LAParams
                from pdfminer.converter import PDFPageAggregator
            except ImportError:
                # Nếu pdfminer chưa được cài đặt, thử cài đặt
                logger.warning("Thư viện pdfminer chưa được cài đặt, đang thử cài đặt...")
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "pdfminer.six"])
                from pdfminer.pdfparser import PDFParser
                from pdfminer.pdfdocument import PDFDocument
                from pdfminer.pdfpage import PDFPage
                from pdfminer.pdfinterp import PDFResourceManager
                from pdfminer.pdfinterp import PDFPageInterpreter
                from pdfminer.layout import LAParams
                from pdfminer.converter import PDFPageAggregator
            
            # Gọi phương thức extract_text_chunks để trích xuất text
            text_chunks = ModelInstance.value.extract_text_chunks(file_data)
            
            # Kiểm tra kết quả trả về
            if not text_chunks or not text_chunks.get('pages'):
                logger.warning("extract_text_chunks trả về kết quả rỗng hoặc không hợp lệ")
                
                # Tạo cấu trúc mặc định nếu kết quả rỗng
                if not text_chunks:
                    text_chunks = {'pages': []}
                
                # Thêm thông tin trang nếu không có
                if len(text_chunks['pages']) == 0:
                    # Sử dụng pymupdf để lấy thông tin trang
                    from pymupdf import Document
                    doc = Document(stream=file_data)
                    for page_idx, page in enumerate(doc):
                        text_chunks['pages'].append({
                            'page_number': page_idx + 1,
                            'width': page.rect.width,
                            'height': page.rect.height,
                            'chunks': []
                        })
            
            return jsonify(text_chunks)
        except Exception as e:
            logger.exception("Lỗi khi trích xuất văn bản")
            return jsonify({
                'error': f'Lỗi khi trích xuất văn bản: {str(e)}',
                'details': str(e.__class__.__name__)
            }), 500
        
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