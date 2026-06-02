import base64
import os
import io
from typing import Optional

_pytesseract_available = False
_pil_available = False
_pymupdf_available = False

try:
    import pytesseract
    _pytesseract_available = True
except ImportError:
    pass

try:
    from PIL import Image
    _pil_available = True
except ImportError:
    pass

try:
    import fitz
    _pymupdf_available = True
except ImportError:
    pass


def is_ocr_available() -> bool:
    if not (_pytesseract_available and _pil_available):
        return False
    try:
        version = pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image(file_path: str) -> Optional[str]:
    if not is_ocr_available():
        return None

    try:
        img = Image.open(file_path)
        text = pytesseract.image_to_string(img)
        return text.strip() if text.strip() else None
    except Exception:
        return None


def ocr_image_base64(base64_data: str) -> Optional[str]:
    if not is_ocr_available():
        return None

    try:
        img_bytes = base64.b64decode(base64_data)
        img = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img)
        return text.strip() if text.strip() else None
    except Exception:
        return None


def extract_text_from_pdf(file_path: str) -> Optional[str]:
    if not _pymupdf_available:
        return None

    try:
        doc = fitz.open(file_path)
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        text = "\n".join(text_parts).strip()
        return text if text else None
    except Exception:
        return None


def extract_text_from_file(file_path: str) -> Optional[str]:
    text_extensions = {
        '.txt', '.csv', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini',
        '.cfg', '.log', '.md', '.py', '.js', '.ts', '.jsx', '.tsx',
        '.html', '.css', '.scss', '.less', '.sh', '.bash', '.zsh',
        '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp',
        '.sql', '.r', '.rb', '.php', '.lua', '.swift', '.kt',
        '.tf', '.env', '.gitignore', '.dockerfile', '.makefile',
        '.conf', '.cnf', '.gradle', '.properties', '.lock',
    }
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in text_extensions:
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if len(content) > 50000:
            content = content[:50000] + "\n\n... (truncated)"
        return content
    except Exception:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
            if len(content) > 50000:
                content = content[:50000] + "\n\n... (truncated)"
            return content
        except Exception:
            return None


IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
ALLOWED_UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS.union({
    '.txt', '.csv', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini',
    '.cfg', '.log', '.md', '.py', '.js', '.ts', '.jsx', '.tsx',
    '.html', '.css', '.scss', '.less', '.sh', '.bash', '.zsh',
    '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp',
    '.sql', '.r', '.rb', '.php', '.lua', '.swift', '.kt',
    '.tf', '.env', '.gitignore', '.dockerfile', '.makefile',
    '.conf', '.cnf', '.gradle', '.properties', '.lock',
    '.pdf',
})


def is_image_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS


def is_allowed_file(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in ALLOWED_UPLOAD_EXTENSIONS


def process_uploaded_file(file_path: str, filename: str, force_ocr: bool = False) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    result = {
        "filename": filename,
        "file_path": file_path,
        "file_type": ext,
        "file_size": os.path.getsize(file_path),
        "ocr_text": None,
    }

    if ext in IMAGE_EXTENSIONS:
        if force_ocr:
            result["ocr_text"] = ocr_image(file_path)
        return result

    if ext == '.pdf':
        text = extract_text_from_pdf(file_path)
        if text:
            result["ocr_text"] = text
        return result

    text = extract_text_from_file(file_path)
    if text:
        result["ocr_text"] = text
    return result
