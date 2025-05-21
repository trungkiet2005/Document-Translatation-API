import os
import logging
import io
import re
from typing import Tuple, Optional, Callable, Any, List, Dict
import fitz  # PyMuPDF
from .doclayout import OnnxModel
from googletrans import Translator

logger = logging.getLogger(__name__)

# Khởi tạo translator
translator = Translator()

# Font mặc định cho tiếng Việt - canonical Base-14 name
DEFAULT_VI_FONT = "helv"

def get_system_fonts() -> List[str]:
    """
    Lấy danh sách font có sẵn trong hệ thống
    
    Returns:
        List[str]: Danh sách tên font
    """
    try:
        fonts = fitz.get_fonts()
        return [f[3] for f in fonts]  # Lấy tên font
    except Exception as e:
        logger.warning(f"Không thể lấy danh sách font: {str(e)}")
        return []

def check_font_support(font_name: str, text: str) -> bool:
    """
    Kiểm tra font có hỗ trợ text không
    
    Args:
        font_name: Tên font
        text: Text cần kiểm tra (nên là một mẫu ngắn, ví dụ "test")
        
    Returns:
        bool: True nếu font hỗ trợ text
    """
    if not font_name: # Explicitly handle None or empty font_name
        logger.debug(f"check_font_support received invalid font_name: '{font_name}'")
        return False
    if not text: # Cannot check support for empty text effectively
        return True # Assume supported if text is empty, or handle as an issue if needed
    try:
        doc = fitz.open() # Create a new in-memory PDF for testing
        page = doc.new_page()
        # Use a simple, non-empty ASCII string for font validation
        page.insert_text((0, 10), "test", fontname=font_name, fontsize=10)
        doc.close() # Important to close the test document
        return True
    except Exception as e:
        logger.debug(f"Font check failed for '{font_name}' with test text: {e}")
        return False

def get_supported_font(text_to_check_support_for: str) -> str:
    """
    Lấy font hỗ trợ text từ danh sách ưu tiên các font Base-14.
    
    Args:
        text_to_check_support_for: Text dùng để kiểm tra (nên là "test").
        
    Returns:
        str: Tên font hỗ trợ (một trong các font Base-14).
    """
    # Danh sách font ưu tiên cho tiếng Việt, chỉ canonical Base-14 fonts
    preferred_fonts = [
        "helv",
        "tim", 
        "cour"
    ]
    
    for font in preferred_fonts:
        # Always use "test" for check_font_support if just validating font existence/usability
        if check_font_support(font, "test"): 
            return font
            
    logger.warning(f"No preferred Base-14 font seems supported, falling back to {DEFAULT_VI_FONT}.")
    return DEFAULT_VI_FONT

def clean_text(text: str) -> str:
    """
    Làm sạch text trước khi dịch
    
    Args:
        text: Text cần làm sạch
        
    Returns:
        str: Text đã làm sạch
    """
    # Loại bỏ các ký tự điều khiển
    text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
    
    # Loại bỏ các ký tự đặc biệt không cần thiết
    text = re.sub(r'[^\w\s.,!?;:\'"()\-–—…]', '', text)
    
    # Chuẩn hóa khoảng trắng
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

def split_text(text: str, max_length: int = 4500) -> List[str]:
    """
    Chia nhỏ text thành các đoạn có độ dài phù hợp
    
    Args:
        text: Text cần chia
        max_length: Độ dài tối đa của mỗi đoạn
        
    Returns:
        List[str]: Danh sách các đoạn text
    """
    # Làm sạch text trước khi chia
    text = clean_text(text)
    
    if len(text) <= max_length:
        return [text]
        
    # Chia theo dấu câu
    sentences = re.split(r'([.!?])\s+', text)
    chunks = []
    current_chunk = ""
    
    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        if i + 1 < len(sentences):
            sentence += sentences[i + 1]  # Thêm dấu câu
            
        if len(current_chunk) + len(sentence) + 2 <= max_length:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
            
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks

def translate_stream(
    stream: bytes,
    lang_in: str = "en",
    lang_out: str = "vi",
    service: str = "google",
    thread: int = 4,
    callback: Optional[Callable] = None,
    model: Optional[OnnxModel] = None,
    prompt: Optional[Any] = None,
    user_font_name: str = "",
    font_size_factor: float = 1.0
) -> Tuple[bytes, bytes]:
    """
    Dịch nội dung PDF từ stream
    Args:
        user_font_name: Tên font chữ do người dùng cung cấp cho văn bản đã dịch
    """
    try:
        doc = fitz.open(stream=stream, filetype="pdf")
        mono_doc = fitz.open()
        dual_doc = fitz.open()
        
        for page_idx, page in enumerate(doc):
            mono_page = mono_doc.new_page(width=page.rect.width, height=page.rect.height)
            dual_page = dual_doc.new_page(width=page.rect.width, height=page.rect.height)
            
            mono_page.show_pdf_page(mono_page.rect, doc, page_idx)
            dual_page.show_pdf_page(dual_page.rect, doc, page_idx)
            
            blocks = page.get_text("dict")["blocks"]
            
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            original_span_text = span["text"].strip()
                            if not original_span_text: continue

                            clean_original_text = clean_text(original_span_text)
                            if not clean_original_text: continue
                                    
                            translated_text = translate_text(
                                clean_original_text, lang_in, lang_out, service, prompt
                            )
                            if not translated_text: continue

                            font_for_translated_text = DEFAULT_VI_FONT
                            if user_font_name:
                                if check_font_support(user_font_name, "test"): # Test with simple ASCII
                                    font_for_translated_text = user_font_name
                                else:
                                    logger.warning(f"User font '{user_font_name}' not supported, falling back to {DEFAULT_VI_FONT}.")
                                    # No need to call get_supported_font, DEFAULT_VI_FONT is already the target
                            # No explicit check for DEFAULT_VI_FONT needed here, as it's assumed to be valid Base-14
                            
                            original_pdf_font_name = span.get("font", DEFAULT_VI_FONT) # Get original font, fallback to default
                            font_for_original_text = DEFAULT_VI_FONT # Start with default
                            if check_font_support(original_pdf_font_name, "test"): # Test original font
                                font_for_original_text = original_pdf_font_name
                            else:
                                logger.warning(f"Original PDF font '{original_pdf_font_name}' not supported, using {DEFAULT_VI_FONT} for original text.")
                            
                            new_size = span["size"] * font_size_factor
                            
                            try:
                                mono_page.insert_text(
                                    span["origin"], translated_text,
                                    fontname=font_for_translated_text, fontsize=new_size, color=span["color"]
                                )
                                dual_page.insert_text(
                                    span["origin"], original_span_text,
                                    fontname=font_for_original_text, fontsize=span["size"], color=span["color"]
                                )
                                # Tăng khoảng cách giữa văn bản gốc và bản dịch (thêm hệ số 2.5)
                                dual_page.insert_text(
                                    (span["origin"][0], span["origin"][1] + span["size"] * 2.5),
                                    translated_text,
                                    fontname=font_for_translated_text, fontsize=new_size, color=span["color"]
                                )
                            except Exception as e:
                                logger.warning(f"Lỗi khi thêm text (fonts: T='{font_for_translated_text}', O='{font_for_original_text}'): {str(e)}. Falling back to {DEFAULT_VI_FONT} for both.")
                                try:
                                    mono_page.insert_text(span["origin"], translated_text, fontname=DEFAULT_VI_FONT, fontsize=new_size, color=span["color"])
                                    dual_page.insert_text(span["origin"], original_span_text, fontname=DEFAULT_VI_FONT, fontsize=span["size"], color=span["color"])
                                    # Tăng khoảng cách giữa văn bản gốc và bản dịch (thêm hệ số 2.5)
                                    dual_page.insert_text((span["origin"][0], span["origin"][1] + span["size"] * 2.5), translated_text, fontname=DEFAULT_VI_FONT, fontsize=new_size, color=span["color"])
                                except Exception as e2:
                                    logger.error(f"Lỗi khi thêm text với font mặc định {DEFAULT_VI_FONT}: {str(e2)}")
            
            if callback: callback(page_idx + 1)
        
        mono_data = mono_doc.write()
        dual_data = dual_doc.write()
        doc.close(); mono_doc.close(); dual_doc.close()
        return mono_data, dual_data
        
    except Exception as e:
        logger.exception("Lỗi khi dịch PDF") # This will log the full traceback for the original error
        raise

def translate_text(
    text: str,
    lang_in: str,
    lang_out: str,
    service: str = "google",
    prompt: Optional[Any] = None
) -> str:
    """
    Dịch text sử dụng dịch vụ được chọn
    
    Args:
        text: Text cần dịch
        lang_in: Ngôn ngữ nguồn
        lang_out: Ngôn ngữ đích
        service: Dịch vụ dịch
        prompt: Template prompt cho việc dịch
        
    Returns:
        str: Text đã dịch
    """
    try:
        # Làm sạch text trước khi dịch
        text_cleaned = clean_text(text) # Use a different variable name
        if not text_cleaned:
            return ""
            
        if service == "google":
            # Chia nhỏ text nếu quá dài
            chunks = split_text(text_cleaned)
            
            # Dịch từng đoạn
            translated_chunks = []
            for chunk in chunks:
                try:
                    result = translator.translate(chunk, src=lang_in, dest=lang_out)
                    translated_chunks.append(result.text)
                except Exception as e:
                    logger.warning(f"Lỗi khi dịch đoạn: {str(e)}")
                    translated_chunks.append(chunk) # Return original chunk on error
                
            return " ".join(translated_chunks)
            
        # Placeholder for other services
        elif service == "bing": return text_cleaned 
        elif service == "deepl": return text_cleaned
        elif service == "openai": return text_cleaned
        elif service == "gemini": return text_cleaned
            
        else: # Fallback to Google for unknown service
            logger.warning(f"Không hỗ trợ dịch vụ {service}, sử dụng Google Translate")
            chunks = split_text(text_cleaned)
            translated_chunks = []
            for chunk in chunks:
                try:
                    result = translator.translate(chunk, src=lang_in, dest=lang_out)
                    translated_chunks.append(result.text)
                except Exception as e:
                    logger.warning(f"Lỗi khi dịch đoạn (fallback): {str(e)}")
                    translated_chunks.append(chunk)
            return " ".join(translated_chunks)
            
    except Exception as e:
        logger.exception(f"Lỗi khi dịch text: {str(e)}")
        return text # Return original text on major error 