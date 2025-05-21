"""Functions that can be used for the most common use-cases for code_pdf.six"""

import asyncio
import io
import os
import re
import sys
import tempfile
import logging
from asyncio import CancelledError
from pathlib import Path
from string import Template
from typing import Any, BinaryIO, List, Optional, Dict

import numpy as np
import requests
import tqdm
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfexceptions import PDFValueError
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pymupdf import Document, Font
import pikepdf  # Add the missing pikepdf import

from code_pdf.converter import TranslateConverter
from code_pdf.doclayout import OnnxModel
from code_pdf.pdfinterp import PDFPageInterpreterEx

from code_pdf.config import ConfigManager
from babeldoc.assets.assets import get_font_and_metadata

NOTO_NAME = "noto"

logger = logging.getLogger(__name__)

noto_list = [
    "am",  # Amharic
    "ar",  # Arabic
    "bn",  # Bengali
    "bg",  # Bulgarian
    "chr",  # Cherokee
    "el",  # Greek
    "gu",  # Gujarati
    "iw",  # Hebrew
    "hi",  # Hindi
    "kn",  # Kannada
    "ml",  # Malayalam
    "mr",  # Marathi
    "ru",  # Russian
    "sr",  # Serbian
    "ta",  # Tamil
    "te",  # Telugu
    "th",  # Thai
    "ur",  # Urdu
    "uk",  # Ukrainian
]


def check_files(files: List[str]) -> List[str]:
    files = [
        f for f in files if not f.startswith("http://")
    ]  # exclude online files, http
    files = [
        f for f in files if not f.startswith("https://")
    ]  # exclude online files, https
    missing_files = [file for file in files if not os.path.exists(file)]
    return missing_files


def translate_patch(
    inf: BinaryIO,
    pages: Optional[list[int]] = None,
    vfont: str = "",
    vchar: str = "",
    thread: int = 0,
    doc_zh: Document = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    noto_name: str = "",
    noto: Font = None,
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    font_name: str = "",
    font_size_factor: float = 1.0,
    **kwarg: Any,
) -> dict:
    rsrcmgr = PDFResourceManager()
    layout = {}
    device = TranslateConverter(
        rsrcmgr,
        vfont,
        vchar,
        thread,
        layout,
        lang_in,
        lang_out,
        service,
        noto_name,
        noto,
        envs,
        prompt,
        font_name,
        font_size_factor,
    )

    assert device is not None
    obj_patch = {}
    interpreter = PDFPageInterpreterEx(rsrcmgr, device, obj_patch)
    if pages:
        total_pages = len(pages)
    else:
        total_pages = doc_zh.page_count

    parser = PDFParser(inf)
    doc = PDFDocument(parser)
    with tqdm.tqdm(total=total_pages) as progress:
        for pageno, page in enumerate(PDFPage.create_pages(doc)):
            if cancellation_event and cancellation_event.is_set():
                raise CancelledError("task cancelled")
            if pages and (pageno not in pages):
                continue
            progress.update()
            if callback:
                callback(progress)
            page.pageno = pageno
            pix = doc_zh[page.pageno].get_pixmap()
            image = np.fromstring(pix.samples, np.uint8).reshape(
                pix.height, pix.width, 3
            )[:, :, ::-1]
            page_layout = model.predict(image, imgsz=int(pix.height / 32) * 32)[0]
            # kdtree là không thể, tốt hơn là render thành hình ảnh, dùng không gian đổi lấy thời gian
            box = np.ones((pix.height, pix.width))
            h, w = box.shape
            vcls = ["abandon", "figure", "table", "isolate_formula", "formula_caption"]
            for i, d in enumerate(page_layout.boxes):
                if page_layout.names[int(d.cls)] not in vcls:
                    x0, y0, x1, y1 = d.xyxy.squeeze()
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    box[y0:y1, x0:x1] = i + 2
            for i, d in enumerate(page_layout.boxes):
                if page_layout.names[int(d.cls)] in vcls:
                    x0, y0, x1, y1 = d.xyxy.squeeze()
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    box[y0:y1, x0:x1] = 0
            layout[page.pageno] = box
            # 新建一个 xref 存放新指令流
            page.page_xref = doc_zh.get_new_xref()  # hack 插入页面的新 xref
            doc_zh.update_object(page.page_xref, "<<>>")
            doc_zh.update_stream(page.page_xref, b"")
            doc_zh[page.pageno].set_contents(page.page_xref)
            interpreter.process_page(page)

    device.close()
    return obj_patch


def translate_stream(
    stream: bytes,
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    font_name: str = "",
    font_size_factor: float = 1.0,
    **kwarg: Any,
):
    font_list = [("tiro", None)]
    noto = None  # Initialize noto variable
    noto_name = NOTO_NAME

    # Nếu có font tùy chọn được chỉ định
    if font_name and font_name.strip():
        try:
            # Xử lý tên font để loại bỏ khoảng trắng
            safe_font_name = font_name.replace(" ", "_")
            
            # Tìm kiếm font trong hệ thống hoặc tải về nếu cần
            font_path = download_remote_fonts(lang_out, font_name)
            
            if font_path:
                # Sử dụng tên file không có phần mở rộng làm tên font an toàn
                safe_font_name = os.path.basename(font_path).split('.')[0].replace(" ", "_")
                
                # Thêm font vào đầu danh sách để ưu tiên
                font_list.insert(0, (safe_font_name, font_path))
                logger.info(f"Sử dụng font tùy chỉnh: {safe_font_name} từ {font_path}")
            else:
                logger.warning(f"Không tìm thấy font '{font_name}', sẽ sử dụng font mặc định")
        except Exception as e:
            logger.warning(f"Không thể sử dụng font tùy chỉnh: {font_name}. Lỗi: {str(e)}")

    # Tải font mặc định cho ngôn ngữ
    try:
        font_path = download_remote_fonts(lang_out.lower())
        noto_name = NOTO_NAME
        noto = Font(noto_name, font_path)
        font_list.append((noto_name, font_path))
    except Exception as e:
        logger.warning(f"Không thể tải font cho ngôn ngữ {lang_out}: {str(e)}")
        # Sử dụng font dự phòng nếu không tải được font cho ngôn ngữ cụ thể
        try:
            font_path = download_remote_fonts("en")
            noto_name = NOTO_NAME
            noto = Font(noto_name, font_path)
            font_list.append((noto_name, font_path))
        except Exception:
            logger.error("Không thể tải font dự phòng. Quá trình dịch có thể bị ảnh hưởng.")

    doc_en = Document(stream=stream)
    stream = io.BytesIO()
    doc_en.save(stream)
    doc_zh = Document(stream=stream)
    page_count = doc_zh.page_count

    # Thêm font vào từng trang
    font_id = {}
    for page in doc_zh:
        for font in font_list:
            try:
                # Kiểm tra tính hợp lệ của tên font và đường dẫn
                if not font[0] or (font[1] and not os.path.isfile(font[1])):
                    continue
                
                # Thử thêm font vào trang
                font_id[font[0]] = page.insert_font(font[0], font[1])
                logger.debug(f"Đã thêm font {font[0]} thành công")
            except ValueError as e:
                logger.error(f"Lỗi khi thêm font {font[0]}: {str(e)}")
                # Bỏ qua font lỗi và tiếp tục với các font khác
                continue
            except Exception as e:
                logger.error(f"Lỗi không xác định khi thêm font {font[0]}: {str(e)}")
                continue

    # Nếu không có font nào được thêm thành công, sử dụng font mặc định của hệ thống
    if not font_id:
        logger.warning("Không thể thêm bất kỳ font nào. Sử dụng font mặc định của hệ thống.")

    # Phần còn lại không đổi
    xreflen = doc_zh.xref_length()
    for xref in range(1, xreflen):
        for label in ["Resources/", ""]:
            try:
                font_res = doc_zh.xref_get_key(xref, f"{label}Font")
                target_key_prefix = f"{label}Font/"
                if font_res[0] == "xref":
                    resource_xref_id = re.search("(\\d+) 0 R", font_res[1])
                    if resource_xref_id:
                        xref = int(resource_xref_id.group(1))
                        font_res = ("dict", doc_zh.xref_object(xref))
                        target_key_prefix = ""

                if font_res[0] == "dict":
                    for font in font_list:
                        if font[0] not in font_id:
                            continue
                        target_key = f"{target_key_prefix}{font[0]}"
                        font_exist = doc_zh.xref_get_key(xref, target_key)
                        if font_exist[0] == "null":
                            doc_zh.xref_set_key(
                                xref,
                                target_key,
                                f"{font_id[font[0]]} 0 R",
                            )
            except Exception as e:
                logger.debug(f"Bỏ qua lỗi xref: {str(e)}")

    fp = io.BytesIO()

    doc_zh.save(fp)
    obj_patch: dict = translate_patch(fp, **locals())

    for obj_id, ops_new in obj_patch.items():
        doc_zh.update_stream(obj_id, ops_new.encode())

    doc_en.insert_file(doc_zh)
    for id in range(page_count):
        doc_en.move_page(page_count + id, id * 2 + 1)
    if not skip_subset_fonts:
        try:
            doc_zh.subset_fonts(fallback=True)
            doc_en.subset_fonts(fallback=True)
        except Exception as e:
            logger.warning(f"Không thể tạo tập hợp con font: {str(e)}")
    
    return (
        doc_zh.write(deflate=True, garbage=3, use_objstms=1),
        doc_en.write(deflate=True, garbage=3, use_objstms=1),
    )


def convert_to_pdfa(input_path, output_path):
    """
    Convert PDF to PDF/A format

    Args:
        input_path: Path to source PDF file
        output_path: Path to save PDF/A file
    """
    from pikepdf import Dictionary, Name, Pdf

    # Open the PDF file
    pdf = Pdf.open(input_path)

    # Add PDF/A conformance metadata
    metadata = {
        "pdfa_part": "2",
        "pdfa_conformance": "B",
        "title": pdf.docinfo.get("/Title", ""),
        "author": pdf.docinfo.get("/Author", ""),
        "creator": "PDF Math Translate",
    }

    with pdf.open_metadata() as meta:
        meta.load_from_docinfo(pdf.docinfo)
        meta["pdfaid:part"] = metadata["pdfa_part"]
        meta["pdfaid:conformance"] = metadata["pdfa_conformance"]

    # Create OutputIntent dictionary
    output_intent = Dictionary(
        {
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFA1"),
            "/OutputConditionIdentifier": "sRGB IEC61966-2.1",
            "/RegistryName": "http://www.color.org",
            "/Info": "sRGB IEC61966-2.1",
        }
    )

    # Add output intent to PDF root
    if "/OutputIntents" not in pdf.Root:
        pdf.Root.OutputIntents = [output_intent]
    else:
        pdf.Root.OutputIntents.append(output_intent)

    # Save as PDF/A
    pdf.save(output_path, linearize=True)
    pdf.close()


def translate(
    files: list[str],
    output: str = "",
    pages: Optional[list[int]] = None,
    lang_in: str = "",
    lang_out: str = "",
    service: str = "",
    thread: int = 0,
    vfont: str = "",
    vchar: str = "",
    callback: object = None,
    compatible: bool = False,
    cancellation_event: asyncio.Event = None,
    model: OnnxModel = None,
    envs: Dict = None,
    prompt: Template = None,
    skip_subset_fonts: bool = False,
    **kwarg: Any,
):
    if not files:
        raise PDFValueError("No files to process.")

    missing_files = check_files(files)

    if missing_files:
        print("The following files do not exist:", file=sys.stderr)
        for file in missing_files:
            print(f"  {file}", file=sys.stderr)
        raise PDFValueError("Some files do not exist.")

    result_files = []

    for file in files:
        if type(file) is str and (
            file.startswith("http://") or file.startswith("https://")
        ):
            print("Online files detected, downloading...")
            try:
                r = requests.get(file, allow_redirects=True)
                if r.status_code == 200:
                    with tempfile.NamedTemporaryFile(
                        suffix=".pdf", delete=False
                    ) as tmp_file:
                        print(f"Writing the file: {file}...")
                        tmp_file.write(r.content)
                        file = tmp_file.name
                else:
                    r.raise_for_status()
            except Exception as e:
                raise PDFValueError(
                    f"Errors occur in downloading the PDF file. Please check the link(s).\nError:\n{e}"
                )
        filename = os.path.splitext(os.path.basename(file))[0]

        # If the commandline has specified converting to PDF/A format
        # --compatible / -cp
        if compatible:
            with tempfile.NamedTemporaryFile(
                suffix="-pdfa.pdf", delete=False
            ) as tmp_pdfa:
                print(f"Converting {file} to PDF/A format...")
                convert_to_pdfa(file, tmp_pdfa.name)
                doc_raw = open(tmp_pdfa.name, "rb")
                os.unlink(tmp_pdfa.name)
        else:
            doc_raw = open(file, "rb")
        s_raw = doc_raw.read()
        doc_raw.close()

        if file.startswith(tempfile.gettempdir()):
            os.unlink(file)
        s_mono, s_dual = translate_stream(
            s_raw,
            **locals(),
        )
        file_mono = Path(output) / f"{filename}-mono.pdf"
        file_dual = Path(output) / f"{filename}-dual.pdf"
        doc_mono = open(file_mono, "wb")
        doc_dual = open(file_dual, "wb")
        doc_mono.write(s_mono)
        doc_dual.write(s_dual)
        doc_mono.close()
        doc_dual.close()
        result_files.append((str(file_mono), str(file_dual)))

    return result_files


def download_remote_fonts(lang: str, custom_font=""):
    """
    Download or locate fonts for the specified language or custom font
    
    Args:
        lang: Language code
        custom_font: Optional custom font name to search for
    
    Returns:
        Path to the font file
    """
    # Check for custom font first if specified
    if custom_font:
        # Common font locations on Windows
        common_font_paths = [
            os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft\\Windows\\Fonts"),
            os.path.join(os.path.expanduser("~"), "AppData\\Local\\Microsoft\\Windows\\Fonts"),
        ]
        
        # Cache directory for babeldoc fonts
        babeldoc_font_dir = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "babeldoc", "fonts")
        os.makedirs(babeldoc_font_dir, exist_ok=True)
        
        # Add babeldoc font directory to search paths
        common_font_paths.append(babeldoc_font_dir)
        
        # Common font file extensions to check
        extensions = [".ttf", ".otf", ".TTF", ".OTF"]
        
        # Normalize font name - remove spaces and convert to lowercase
        custom_font_lower = custom_font.lower().replace(" ", "").replace("_", "").replace("-", "")
        
        # Font name variants to check
        font_variants = [
            custom_font_lower,
            f"{custom_font_lower}regular",
            f"{custom_font_lower}mt",
            f"{custom_font_lower}std"
        ]
        
        # Check in system font directories
        for font_dir in common_font_paths:
            if os.path.exists(font_dir):
                logger.debug(f"Checking for fonts in: {font_dir}")
                try:
                    for file in os.listdir(font_dir):
                        file_lower = file.lower().replace(" ", "").replace("_", "").replace("-", "")
                        file_base = os.path.splitext(file_lower)[0]
                        
                        # Check if file matches any of our font variants
                        if any(variant in file_base for variant in font_variants) and any(file.lower().endswith(ext.lower()) for ext in extensions):
                            font_path = os.path.join(font_dir, file)
                            logger.info(f"Found font for '{custom_font}': {font_path}")
                            return font_path
                except Exception as e:
                    logger.warning(f"Error checking font directory {font_dir}: {str(e)}")
        
        # Common fonts mapping - these are fonts we know about and can download or select
        common_fonts = {
            "roboto": {
                "filename": "Roboto-Regular.ttf",
                "url": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Regular.ttf"
            },
            "arial": {
                "filename": "Arial.ttf",
                "url": "https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial.ttf"
            },
            "times": {
                "filename": "Times.ttf",
                "url": None  # No direct download link
            },
            "timesnewroman": {
                "filename": "TimesNewRoman.ttf",
                "url": None
            },
            "verdana": {
                "filename": "Verdana.ttf",
                "url": None
            }
        }
        
        # Find matching font
        matched_font = None
        for font_key, font_info in common_fonts.items():
            if font_key in custom_font_lower:
                matched_font = font_info
                break
        
        if matched_font:
            target_path = os.path.join(babeldoc_font_dir, matched_font["filename"])
            
            # Check if font already exists in our cache
            if os.path.exists(target_path):
                logger.info(f"Found cached font: {target_path}")
                return target_path
            
            # Try to download the font if URL is available
            if matched_font["url"]:
                try:
                    url = matched_font["url"]
                    logger.info(f"Downloading font {matched_font['filename']} from {url}")
                    
                    import requests
                    r = requests.get(url, allow_redirects=True)
                    
                    if r.status_code == 200:
                        # Save the font file
                        with open(target_path, 'wb') as f:
                            f.write(r.content)
                        
                        if os.path.exists(target_path):
                            logger.info(f"Successfully downloaded and installed font: {matched_font['filename']}")
                            return target_path
                except Exception as e:
                    logger.warning(f"Failed to download {matched_font['filename']}: {str(e)}")
    
    # Proceed with language-specific fonts if custom font not found
    lang = lang.lower()
    LANG_NAME_MAP = {
        **{la: "GoNotoKurrent-Regular.ttf" for la in noto_list},
        **{
            la: f"SourceHanSerif{region}-Regular.ttf"
            for region, langs in {
                "CN": ["zh-cn", "zh-hans", "zh"],
                "TW": ["zh-tw", "zh-hant"],
                "JP": ["ja"],
                "KR": ["ko"],
            }.items()
            for la in langs
        },
    }
    font_name = LANG_NAME_MAP.get(lang, "GoNotoKurrent-Regular.ttf")

    # Try to get font from docker path or using babeldoc
    font_path = ConfigManager.get("NOTO_FONT_PATH", Path("/app", font_name).as_posix())
    if not Path(font_path).exists():
        try:
            font_path, _ = get_font_and_metadata(font_name)
            font_path = font_path.as_posix()
            logger.info(f"Using language font: {font_path}")
        except Exception as e:
            logger.warning(f"Could not get font for language {lang}: {str(e)}")
            # Try English font as fallback
            try:
                fallback_font_name = "GoNotoKurrent-Regular.ttf"
                font_path, _ = get_font_and_metadata(fallback_font_name)
                font_path = font_path.as_posix()
                logger.info(f"Using fallback font: {font_path}")
            except Exception as e:
                logger.error(f"Failed to get fallback font: {str(e)}")
                # Return None and let the caller handle it
                return None

    return font_path
