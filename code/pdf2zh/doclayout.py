import os
import logging
import numpy as np
from typing import Dict, List, Optional, Union, Any
import io
import json
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

class ModelInstance:
    """Singleton class để lưu trữ instance của mô hình"""
    value = None

class OnnxModel:
    """Class xử lý layout của PDF sử dụng ONNX model"""
    
    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path
        self.model = None
        self._load_model()
    
    def _load_model(self):
        """Load ONNX model"""
        try:
            import onnxruntime as ort
            if self.model_path and os.path.exists(self.model_path):
                self.model = ort.InferenceSession(self.model_path)
            else:
                logger.warning("Không tìm thấy model path, sử dụng model mặc định")
                # Tạo một model giả để test
                self.model = None
        except Exception as e:
            logger.error(f"Lỗi khi load model: {str(e)}")
            self.model = None
    
    @classmethod
    def load_available(cls) -> 'OnnxModel':
        """Load model có sẵn trong hệ thống"""
        # Tìm model trong các thư mục cache
        cache_dirs = [
            os.path.join(os.environ.get("XDG_CACHE_HOME", "/tmp/.cache"), "pdf2zh"),
            os.path.join(os.environ.get("HOME", "/tmp"), ".cache", "pdf2zh"),
        ]
        
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                for file in os.listdir(cache_dir):
                    if file.endswith(".onnx"):
                        return cls(os.path.join(cache_dir, file))
        
        # Nếu không tìm thấy, trả về model mặc định
        return cls()
    
    def extract_text_chunks(self, pdf_data: bytes) -> Dict[str, Any]:
        """Trích xuất các đoạn văn bản và bounding boxes từ PDF"""
        try:
            import fitz  # PyMuPDF
            
            # Đọc PDF từ bytes
            doc = fitz.open(stream=pdf_data, filetype="pdf")
            
            # Kết quả
            result = {
                "pages": []
            }
            
            # Xử lý từng trang
            for page_idx, page in enumerate(doc):
                # Lấy text và blocks
                blocks = page.get_text("dict")["blocks"]
                
                # Lọc các block chứa text
                text_blocks = []
                for block in blocks:
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line["spans"]:
                                if span["text"].strip():
                                    text_blocks.append({
                                        "text": span["text"],
                                        "bbox": [
                                            span["bbox"][0],  # x0
                                            span["bbox"][1],  # y0
                                            span["bbox"][2],  # x1
                                            span["bbox"][3]   # y1
                                        ],
                                        "font": span.get("font", ""),
                                        "size": span.get("size", 0)
                                    })
                
                # Thêm thông tin trang
                result["pages"].append({
                    "page_number": page_idx + 1,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "chunks": text_blocks
                })
            
            return result
            
        except Exception as e:
            logger.exception("Lỗi khi trích xuất text chunks")
            return {"pages": []} 