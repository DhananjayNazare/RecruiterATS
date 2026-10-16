"""Resume / JD / photo uploads and text extraction."""
import re
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
from recruiter_ats import config
from recruiter_ats.config import ALLOWED_EXTENSIONS, ALLOWED_IMAGE_EXTENSIONS, Image, MAX_RESUME_BYTES


def allowed_file(name): return "." in name and name.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_image(name): return "." in name and name.rsplit(".",1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def compress_candidate_photo(file_storage, output_name):
    """Save candidate photo as JPEG <=20 KB when Pillow is available."""
    target = config.UPLOAD_DIR / output_name
    if Image is None:
        file_storage.save(target)
        return output_name
    from io import BytesIO
    raw = file_storage.read()
    with Image.open(BytesIO(raw)) as im:
        im = im.convert("RGB")
        max_dim = 900
        scale = min(1.0, max_dim / max(im.size))
        if scale < 1:
            im = im.resize((max(1, int(im.width*scale)), max(1, int(im.height*scale))), Image.LANCZOS)
        # Find the highest JPEG quality that fits under 20 KB.
        best = None
        w, h = im.size
        while True:
            lo, hi = 10, 85
            local = None
            for _ in range(8):
                q = (lo + hi) // 2
                buf = BytesIO(); im.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                if buf.tell() <= 20*1024:
                    local = buf.getvalue(); lo = q + 1
                else:
                    hi = q - 1
            if local is not None:
                best = local
                break
            if w <= 120 or h <= 120:
                buf = BytesIO(); im.save(buf, format="JPEG", quality=10, optimize=True)
                best = buf.getvalue()
                break
            w=max(120,int(w*0.8)); h=max(120,int(h*0.8))
            im=im.resize((w,h), Image.LANCZOS)
        target.write_bytes(best)
    return output_name

def save_resume_upload(file_storage, destination):
    """Save a resume only when its actual byte size is <= 2 MiB."""
    max_bytes = MAX_RESUME_BYTES
    stream = file_storage.stream
    try:
        stream.seek(0)
    except Exception:
        pass
    total = 0
    with open(destination, "wb") as out:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out.close()
                Path(destination).unlink(missing_ok=True)
                raise ValueError("Resume file exceeds the 2 MB maximum size limit.")
            out.write(chunk)
    if total == 0:
        Path(destination).unlink(missing_ok=True)
        raise ValueError("Resume file is empty.")
    return total

def clean_resume_text(text):
    """Return plain text with layout noise and excessive whitespace removed."""
    text = text or ""
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove repeated page/header separators and other non-informative runs.
    text = re.sub(r"(?:[._-]\s*){5,}", " ", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()

def summarize_resume_text(text, max_chars=3500):
    """Create a short extractive resume summary without another model/API call."""
    clean = clean_resume_text(text)
    if not clean:
        return ""
    # Prefer section headings and information-dense sentences, preserving the
    # beginning of the resume where name/contact/summary usually appear.
    parts = re.split(r"(?<=[.!?])\s+|\n", clean)
    parts = [p.strip() for p in parts if p.strip()]
    selected = []
    seen = set()
    for part in parts:
        key = re.sub(r"\W+", "", part.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        if len(part) < 3:
            continue
        selected.append(part)
        if sum(len(x) + 1 for x in selected) >= max_chars:
            break
    summary = " ".join(selected)
    return summary[:max_chars].strip()

def extract_text(path):
    ext=path.suffix.lower()
    try:
        if ext==".txt":
            raw = path.read_text(encoding="utf-8",errors="ignore")
        elif ext==".pdf":
            from pypdf import PdfReader
            raw = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        elif ext==".docx":
            from docx import Document
            doc = Document(str(path))
            chunks = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    chunks.append(" | ".join(cell.text for cell in row.cells))
            raw = "\n".join(chunks)
        else:
            return ""
        return clean_resume_text(raw)
    except Exception as e:
        return f"[Resume extraction error: {e}]"

def save_jd_upload(file_storage):
    """Save an attached JD (PDF/DOCX/TXT, max 2 MB). Returns (stored_name, original_name, text) or raises ValueError."""
    if not allowed_file(file_storage.filename):
        raise ValueError("JD must be a PDF, DOCX or TXT file.")
    original = secure_filename(file_storage.filename)
    stored = f"jd_{uuid.uuid4().hex}_{original}"
    path = config.UPLOAD_DIR / stored
    try:
        save_resume_upload(file_storage, path)
    except ValueError as e:
        raise ValueError(str(e).replace("Resume file", "JD file"))
    text = extract_text(path)
    if not text.strip() or text.startswith("[Resume extraction error"):
        path.unlink(missing_ok=True)
        raise ValueError("The JD file could not be read. Upload a text-readable PDF, DOCX or TXT file.")
    return stored, original, text
