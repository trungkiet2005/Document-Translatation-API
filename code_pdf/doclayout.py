import abc
import os.path

import cv2
import numpy as np
import ast
from babeldoc.assets.assets import get_doclayout_onnx_model_path

try:
    import onnx
    import onnxruntime
except ImportError as e:
    if "DLL load failed" in str(e):
        raise OSError(
            "Microsoft Visual C++ Redistributable is not installed. "
            "Download it at https://aka.ms/vs/17/release/vc_redist.x64.exe"
        ) from e
    raise

from huggingface_hub import hf_hub_download

from code_pdf.config import ConfigManager


class DocLayoutModel(abc.ABC):
    @staticmethod
    def load_onnx():
        model = OnnxModel.from_pretrained()
        return model

    @staticmethod
    def load_available():
        return DocLayoutModel.load_onnx()

    @property
    @abc.abstractmethod
    def stride(self) -> int:
        """Stride of the model input."""
        pass

    @abc.abstractmethod
    def predict(self, image, imgsz=1024, **kwargs) -> list:
        """
        Predict the layout of a document page.

        Args:
            image: The image of the document page.
            imgsz: Resize the image to this size. Must be a multiple of the stride.
            **kwargs: Additional arguments.
        """
        pass


class YoloResult:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, boxes, names):
        self.boxes = [YoloBox(data=d) for d in boxes]
        self.boxes.sort(key=lambda x: x.conf, reverse=True)
        self.names = names


class YoloBox:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, data):
        self.xyxy = data[:4]
        self.conf = data[-2]
        self.cls = data[-1]


class OnnxModel(DocLayoutModel):
    def __init__(self, model_path: str):
        self.model_path = model_path

        model = onnx.load(model_path)
        metadata = {d.key: d.value for d in model.metadata_props}
        self._stride = ast.literal_eval(metadata["stride"])
        self._names = ast.literal_eval(metadata["names"])

        self.model = onnxruntime.InferenceSession(model.SerializeToString())

    @staticmethod
    def from_pretrained():
        pth = get_doclayout_onnx_model_path()
        return OnnxModel(pth)

    @property
    def stride(self):
        return self._stride

    def resize_and_pad_image(self, image, new_shape):
        """
        Resize and pad the image to the specified size, ensuring dimensions are multiples of stride.

        Parameters:
        - image: Input image
        - new_shape: Target size (integer or (height, width) tuple)
        - stride: Padding alignment stride, default 32

        Returns:
        - Processed image
        """
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        h, w = image.shape[:2]
        new_h, new_w = new_shape

        # Calculate scaling ratio
        r = min(new_h / h, new_w / w)
        resized_h, resized_w = int(round(h * r)), int(round(w * r))

        # Resize image
        image = cv2.resize(
            image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR
        )

        # Calculate padding size and align to stride multiple
        pad_w = (new_w - resized_w) % self.stride
        pad_h = (new_h - resized_h) % self.stride
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        # Add padding
        image = cv2.copyMakeBorder(
            image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        return image

    def scale_boxes(self, img1_shape, boxes, img0_shape):
        """
        Rescales bounding boxes (in the format of xyxy by default) from the shape of the image they were originally
        specified in (img1_shape) to the shape of a different image (img0_shape).

        Args:
            img1_shape (tuple): The shape of the image that the bounding boxes are for,
                in the format of (height, width).
            boxes (torch.Tensor): the bounding boxes of the objects in the image, in the format of (x1, y1, x2, y2)
            img0_shape (tuple): the shape of the target image, in the format of (height, width).

        Returns:
            boxes (torch.Tensor): The scaled bounding boxes, in the format of (x1, y1, x2, y2)
        """

        # Calculate scaling ratio
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])

        # Calculate padding size
        pad_x = round((img1_shape[1] - img0_shape[1] * gain) / 2 - 0.1)
        pad_y = round((img1_shape[0] - img0_shape[0] * gain) / 2 - 0.1)

        # Remove padding and scale boxes
        boxes[..., :4] = (boxes[..., :4] - [pad_x, pad_y, pad_x, pad_y]) / gain
        return boxes

    def predict(self, image, imgsz=1024, **kwargs):
        # Preprocess input image
        orig_h, orig_w = image.shape[:2]
        pix = self.resize_and_pad_image(image, new_shape=imgsz)
        pix = np.transpose(pix, (2, 0, 1))  # CHW
        pix = np.expand_dims(pix, axis=0)  # BCHW
        pix = pix.astype(np.float32) / 255.0  # Normalize to [0, 1]
        new_h, new_w = pix.shape[2:]

        # Run inference
        preds = self.model.run(None, {"images": pix})[0]

        # Postprocess predictions
        preds = preds[preds[..., 4] > 0.25]
        preds[..., :4] = self.scale_boxes(
            (new_h, new_w), preds[..., :4], (orig_h, orig_w)
        )
        return [YoloResult(boxes=preds, names=self._names)]

    def extract_text_chunks(self, pdf_bytes):
        """
        Extract text chunks with bounding boxes from a PDF document.
        Each text chunk corresponds to a separate line of text.
        
        Args:
            pdf_bytes: The PDF file data as bytes
            
        Returns:
            Dictionary containing text chunks with their bounding boxes for each page
            Format: {
                'pages': [
                    {
                        'page_number': 1,
                        'width': 612,
                        'height': 792,
                        'chunks': [
                            {'text': 'Line 1 text', 'box': [x, y, width, height]},
                            {'text': 'Line 2 text', 'box': [x, y, width, height]},
                            ...
                        ]
                    },
                    ...
                ]
            }
        """
        try:
            import io
            import logging
            import re
            logger = logging.getLogger(__name__)
            
            try:
                from pymupdf import Document
            except ImportError:
                try:
                    import fitz
                    Document = fitz.open
                except ImportError:
                    logger.error("Không thể import pymupdf hoặc fitz. Hãy cài đặt thư viện: pip install pymupdf")
                    raise
            
            try:
                from pdfminer.pdfparser import PDFParser
                from pdfminer.pdfdocument import PDFDocument
                from pdfminer.pdfpage import PDFPage
                from pdfminer.pdfinterp import PDFResourceManager
                from pdfminer.pdfinterp import PDFPageInterpreter
                from pdfminer.layout import LAParams, LTTextBox, LTText, LTTextLine, LTChar
                from pdfminer.converter import PDFPageAggregator
            except ImportError:
                logger.error("Không thể import pdfminer. Hãy cài đặt thư viện: pip install pdfminer.six")
                raise
                
            import numpy as np
            
            # Store the results
            result = {'pages': []}
            
            # Open PDF with PyMuPDF for page info and images
            try:
                doc_mupdf = Document(stream=pdf_bytes)
            except Exception as e:
                logger.error(f"Lỗi khi mở PDF với PyMuPDF: {e}")
                return result
            
            # Open PDF with pdfminer for text extraction
            pdf_io = io.BytesIO(pdf_bytes)
            parser = PDFParser(pdf_io)
            try:
                doc = PDFDocument(parser)
            except Exception as e:
                logger.error(f"Lỗi khi phân tích PDF với pdfminer: {e}")
                # Vẫn tạo các trang cơ bản mà không có text chunks
                for i, page in enumerate(doc_mupdf):
                    result['pages'].append({
                        'page_number': i + 1,
                        'width': page.rect.width,
                        'height': page.rect.height,
                        'chunks': []
                    })
                return result
                
            rsrcmgr = PDFResourceManager()
            # Sử dụng LAParams với detect_vertical=True để xác định các dòng dọc và ngang
            laparams = LAParams(line_margin=0.5, detect_vertical=True)
            device = PDFPageAggregator(rsrcmgr, laparams=laparams)
            interpreter = PDFPageInterpreter(rsrcmgr, device)
            
            # Process each page
            for page_idx, page_mupdf in enumerate(doc_mupdf):
                # Get the page dimensions from PyMuPDF
                width, height = page_mupdf.rect.width, page_mupdf.rect.height
                
                # Create page entry
                page_data = {
                    'page_number': page_idx + 1,  # 1-based page numbering
                    'width': width,
                    'height': height,
                    'chunks': []
                }
                
                try:
                    # Extract text chunks from the current page using pdfminer
                    for i, page_miner in enumerate(PDFPage.create_pages(doc)):
                        if i == page_idx:
                            interpreter.process_page(page_miner)
                            layout = device.get_result()
                            
                            # Process each element in the layout
                            for lt_obj in layout:
                                if isinstance(lt_obj, LTTextBox):
                                    # Process text box (container of text lines)
                                    for line in lt_obj:
                                        if isinstance(line, LTTextLine):
                                            # Extract line text
                                            text = line.get_text().rstrip()
                                            if not text:
                                                continue
                                                
                                            # Convert pdfminer coordinates (origin at bottom left) to our format (origin at top left)
                                            x0, y0, x1, y1 = line.bbox
                                            # Transform y-coordinates (PDF coordinates start from bottom)
                                            y0, y1 = height - y1, height - y0
                                            
                                            # Add the line as a chunk
                                            page_data['chunks'].append({
                                                'text': text,
                                                'box': [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]  # [x, y, width, height]
                                            })
                            break # Break after finding the correct page
                
                except Exception as e:
                    logger.error(f"Lỗi khi trích xuất văn bản từ trang {page_idx + 1}: {e}")
                    # Thử phương pháp dự phòng với PyMuPDF
                    try:
                        # Sử dụng PyMuPDF để trích xuất văn bản theo từng dòng
                        text_page = page_mupdf.get_textpage()
                        blocks = page_mupdf.get_text("dict")["blocks"]
                        
                        for block in blocks:
                            if "lines" in block:
                                for line in block["lines"]:
                                    if "spans" in line:
                                        line_text = ""
                                        for span in line["spans"]:
                                            line_text += span.get("text", "")
                                        
                                        if line_text.strip():
                                            # Extract bounding box for the line
                                            bbox = line.get("bbox", [0, 0, 0, 0])
                                            x0, y0, x1, y1 = bbox
                                            
                                            page_data['chunks'].append({
                                                'text': line_text.strip(),
                                                'box': [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]
                                            })
                    except Exception as e2:
                        logger.error(f"Phương pháp dự phòng cũng gặp lỗi: {e2}")
                
                # Add page data to result
                result['pages'].append(page_data)
            
            # Kiểm tra nếu không có chunks nào được tìm thấy
            if not any(page.get('chunks') for page in result['pages']):
                logger.warning("Không phát hiện được text chunks nào, thử phương pháp đơn giản hơn")
                try:
                    for page_idx, page in enumerate(doc_mupdf):
                        text = page.get_text()
                        if text.strip():
                            # Phân tách thành từng dòng
                            lines = text.splitlines()
                            y_pos = 0
                            line_height = page.rect.height / (len(lines) or 1)
                            
                            for line in lines:
                                if line.strip():
                                    # Tạo bounding box đơn giản cho từng dòng
                                    page_data = result['pages'][page_idx]
                                    page_data['chunks'].append({
                                        'text': line.strip(),
                                        'box': [0, y_pos, int(page.rect.width), int(line_height)]
                                    })
                                y_pos += line_height
                except Exception as e:
                    logger.error(f"Lỗi khi trích xuất text đơn giản: {e}")
            
            # Kiểm tra và sắp xếp lại các chunks theo thứ tự y (từ trên xuống dưới)
            for page in result['pages']:
                page['chunks'].sort(key=lambda chunk: chunk['box'][1])
            
            return result
            
        except Exception as e:
            logger.error(f"Lỗi không xác định trong extract_text_chunks: {e}")
            # Return minimal result structure
            return {
                'pages': [{
                    'page_number': 1,
                    'width': 612.0,  # Default letter size
                    'height': 792.0,
                    'chunks': []
                }]
            }


class ModelInstance:
    value: OnnxModel = None
