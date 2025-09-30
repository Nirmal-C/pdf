import streamlit as st
import io
import os
import time
import zipfile
from datetime import datetime
import subprocess
import atexit
from typing import List, Dict

# Core PDF libraries
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
import fitz  # PyMuPDF

# Conversion libraries
try:
    from pdf2docx import Converter
    PDF2DOCX_AVAILABLE = True
except ImportError:
    PDF2DOCX_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.utils import ImageReader
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


# ============ STATE HELPERS (Temp + Uploads) =================================
class TempManager:
    def __init__(self):
        if "TMP_PATHS" not in st.session_state:
            st.session_state.TMP_PATHS: List[str] = []

    def create(self, suffix: str = ""):
        import tempfile
        f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        st.session_state.TMP_PATHS.append(f.name)
        return f

    def register(self, path: str):
        if path and path not in st.session_state.TMP_PATHS:
            st.session_state.TMP_PATHS.append(path)

    def cleanup(self):
        kept = []
        for p in list(st.session_state.TMP_PATHS):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                kept.append(p)
        st.session_state.TMP_PATHS = kept


class UploadManager:
    """Tracks all file_uploader widget keys; allows centralized clearing."""
    def __init__(self):
        if "UPLOAD_KEYS" not in st.session_state:
            st.session_state.UPLOAD_KEYS: Dict[str, bool] = {}  # key -> is_multi

    def register(self, key: str, is_multi: bool):
        st.session_state.UPLOAD_KEYS[key] = is_multi

    def clear_all(self):
        # Remove the widget keys so file_uploader resets visually in the frontend
        for key in list(st.session_state.get("UPLOAD_KEYS", {}).keys()):
            st.session_state.pop(key, None)
        # Clear helper states tied to uploads
        for helper_key in ["file_order"]:
            st.session_state.pop(helper_key, None)
        # Reset registry
        st.session_state.UPLOAD_KEYS = {}


TMP = TempManager()
UP = UploadManager()
atexit.register(lambda: TMP.cleanup())
# ============================================================================


# Configure Streamlit page
st.set_page_config(
    page_title="Comprehensive PDF Toolkit",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
    }
    .stButton > button {
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 5px;
        padding: 0.5rem 1rem;
    }
</style>
""", unsafe_allow_html=True)


# ---------- UTILITY FUNCTIONS ----------

def get_pdf_info(pdf_file):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            info = {
                'pages': doc.page_count,
                'title': doc.metadata.get('title', 'Unknown'),
                'author': doc.metadata.get('author', 'Unknown'),
                'creator': doc.metadata.get('creator', 'Unknown'),
                'producer': doc.metadata.get('producer', 'Unknown'),
                'creation_date': doc.metadata.get('creationDate', 'Unknown'),
                'modification_date': doc.metadata.get('modDate', 'Unknown')
            }
            doc.close()
            return info
    except Exception as e:
        st.error(f"Error reading PDF info: {e}")
        return None


def get_pdf_preview(pdf_file, page_num=0, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            if page_num < doc.page_count:
                page = doc[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                img_data = pix.tobytes("png")
                doc.close()
                return img_data
            doc.close()
            return None
    except Exception as e:
        st.error(f"Error generating preview: {e}")
        return None


def show_progress(message, progress=0):
    progress_bar = st.progress(progress)
    status_text = st.empty()
    status_text.text(message)
    return progress_bar, status_text


# ---------- PDF ORGANIZATION FUNCTIONS ----------

def merge_pdfs(files, order, passwords_list):
    """Merge multiple PDFs into one. passwords_list is per original index in files."""
    merger = PdfMerger()
    progress_bar, status_text = show_progress("Starting PDF merge...", 10)
    total_files = len(order)

    for i, idx in enumerate(order):
        file = files[idx]
        filename = file.name
        password = passwords_list[idx] if idx < len(passwords_list) else ""

        try:
            file.seek(0)
            reader = PdfReader(file)

            if reader.is_encrypted:
                if not password:
                    raise ValueError(f"{filename} is encrypted but no password was provided.")
                if reader.decrypt(password) == 0:
                    raise ValueError(f"Incorrect password for {filename}.")

            merger.append(reader)
            progress_percent = int(10 + ((i + 1) / total_files) * 80)
            status_text.text(f"Processing file {i + 1} of {total_files}...")
            progress_bar.progress(progress_percent)
            time.sleep(0.1)

        except Exception as e:
            st.error(f"Error merging file {filename}: {e}")
            return None

    status_text.text("Finalizing merged PDF...")
    progress_bar.progress(90)

    output = io.BytesIO()
    merger.write(output)
    merger.close()
    output.seek(0)

    status_text.text("Merge complete!")
    progress_bar.progress(100)
    time.sleep(0.5)
    progress_bar.empty(); status_text.empty()
    return output.getvalue()


def split_pdf(file, page_ranges, password=None):
    try:
        reader = PdfReader(file)
        if reader.is_encrypted:
            if not password:
                raise ValueError("PDF is encrypted. Password required.")
            if reader.decrypt(password) != 1:
                raise ValueError("Incorrect password.")

        splits = []
        for (start, end) in page_ranges:
            writer = PdfWriter()
            for i in range(start - 1, end):
                if i < len(reader.pages):
                    writer.add_page(reader.pages[i])
            output = io.BytesIO()
            writer.write(output)
            output.seek(0)
            splits.append((f"split_{start}_{end}.pdf", output.read()))
        return splits
    except Exception as e:
        st.error(f"Error splitting PDF: {e}")
        return []


def organize_pdf_pages(pdf_file, new_order, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            new_doc = fitz.open()
            for page_num in new_order:
                if 0 <= page_num < doc.page_count:
                    new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            output = io.BytesIO()
            new_doc.save(output); new_doc.close(); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error organizing PDF: {e}")
        return None


def remove_pdf_pages(pdf_file, pages_to_remove, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            for page_num in sorted(pages_to_remove, reverse=True):
                if 0 <= page_num < doc.page_count:
                    doc.delete_page(page_num)
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error removing pages: {e}")
        return None


def extract_pdf_pages(pdf_file, pages_to_extract, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            new_doc = fitz.open()
            for page_num in sorted(pages_to_extract):
                if 0 <= page_num < doc.page_count:
                    new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            output = io.BytesIO()
            new_doc.save(output); new_doc.close(); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error extracting pages: {e}")
        return None


# ---------- PDF OPTIMIZATION FUNCTIONS ----------

def compress_pdf(pdf_file, quality="medium", password=None):
    try:
        with TMP.create('.pdf') as temp_input:
            temp_input.write(pdf_file.read())
            temp_input_path = temp_input.name
        with TMP.create('.pdf') as temp_output:
            temp_output_path = temp_output.name

        quality_settings = {"high": "/prepress", "medium": "/ebook", "low": "/screen"}

        gs_command = [
            "gs",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={quality_settings.get(quality, '/ebook')}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={temp_output_path}",
            temp_input_path
        ]

        result = subprocess.run(gs_command, capture_output=True, text=True)

        if result.returncode != 0:
            doc = fitz.open(temp_input_path)
            if doc.needs_pass and password:
                doc.authenticate(password)
            output = io.BytesIO()
            doc.save(output, deflate=True, clean=True, garbage=4)
            doc.close()
            output.seek(0)
            return output.getvalue()

        with open(temp_output_path, 'rb') as f:
            compressed_data = f.read()
        return compressed_data

    except Exception as e:
        st.error(f"Error compressing PDF: {e}")
        return None


def optimize_pdf(pdf_file, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True, clean=True)
            doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error optimizing PDF: {e}")
        return None


def repair_pdf(pdf_file, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            new_doc = fitz.open()
            for page_num in range(doc.page_count):
                try:
                    new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                except:
                    st.warning(f"Skipped corrupted page {page_num + 1}")
                    continue
            output = io.BytesIO()
            new_doc.save(output); new_doc.close(); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error repairing PDF: {e}")
        return None


# ---------- OCR FUNCTIONS ----------

def ocr_pdf(pdf_file, language='eng', password=None):
    if not TESSERACT_AVAILABLE:
        st.error("Tesseract OCR is not available. Please install pytesseract.")
        return None
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            new_doc = fitz.open()
            for page_num in range(doc.page_count):
                page = doc[page_num]
                pix = page.get_pixmap()
                img_data = pix.tobytes("png")
                if PIL_AVAILABLE:
                    image = Image.open(io.BytesIO(img_data))
                    text = pytesseract.image_to_string(image, lang=language)
                    new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
                    new_page.insert_image(page.rect, pixmap=pix)
                    if text.strip():
                        new_page.insert_text((50, 50), text, fontsize=1, color=(1, 1, 1))
                else:
                    new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            output = io.BytesIO()
            new_doc.save(output); new_doc.close(); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error performing OCR: {e}")
        return None


# ---------- PDF TO EXCEL ----------

def pdf_to_excel(pdf_file, password=None, extract_method="tables"):
    if not PANDAS_AVAILABLE:
        st.error("Pandas is not available. Please install pandas.")
        return None
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                tables_found = False
                for page_num in range(doc.page_count):
                    page = doc[page_num]
                    if extract_method == "tables":
                        try:
                            tables = page.find_tables()
                        except Exception:
                            tables = []
                        for table_num, table in enumerate(tables):
                            try:
                                table_data = table.extract()
                                if table_data and len(table_data) > 1:
                                    df = pd.DataFrame(table_data[1:], columns=table_data[0])
                                    df = df.dropna(how='all').dropna(axis=1, how='all')
                                    if not df.empty:
                                        sheet_name = f"Page{page_num+1}_Table{table_num+1}"
                                        df.to_excel(writer, sheet_name=sheet_name, index=False)
                                        tables_found = True
                            except Exception:
                                continue
                    else:
                        text = page.get_text()
                        if text.strip():
                            lines = [line.strip() for line in text.split('\n') if line.strip()]
                            potential_tables = []
                            current_table = []
                            for line in lines:
                                if '\t' in line or '|' in line or len(line.split()) > 2:
                                    if '\t' in line:
                                        row = line.split('\t')
                                    elif '|' in line:
                                        row = [cell.strip() for cell in line.split('|') if cell.strip()]
                                    else:
                                        row = line.split()
                                    current_table.append(row)
                                else:
                                    if current_table and len(current_table) > 1:
                                        potential_tables.append(current_table)
                                    current_table = []
                            if current_table and len(current_table) > 1:
                                potential_tables.append(current_table)
                            for table_num, table_data in enumerate(potential_tables):
                                try:
                                    if len(table_data) > 1:
                                        max_cols = max(len(r) for r in table_data)
                                        normalized = [r + ['']*(max_cols-len(r)) for r in table_data]
                                        df = pd.DataFrame(normalized[1:], columns=normalized[0])
                                        df = df.dropna(how='all').dropna(axis=1, how='all')
                                        if not df.empty:
                                            sheet_name = f"Page{page_num+1}_Text{table_num+1}"
                                            df.to_excel(writer, sheet_name=sheet_name, index=False)
                                            tables_found = True
                                except Exception:
                                    continue
                if not tables_found:
                    all_text = []
                    for p in range(doc.page_count):
                        t = doc[p].get_text()
                        if t.strip():
                            all_text.extend([f"=== Page {p+1} ===", t.strip(), ""])
                    if all_text:
                        df = pd.DataFrame({'Content': all_text})
                        df.to_excel(writer, sheet_name='All_Text', index=False)
            doc.close()
            excel_buffer.seek(0)
            return excel_buffer.getvalue()
    except Exception as e:
        st.error(f"Error converting PDF to Excel: {e}")
        return None


def images_to_pdf(image_files):
    if not PIL_AVAILABLE:
        st.error("PIL is not available. Please install Pillow.")
        return None
    try:
        doc = fitz.open()
        for image_file in image_files:
            image = Image.open(image_file)
            if image.mode != 'RGB':
                image = image.convert('RGB')
            with TMP.create('.jpg') as temp_img:
                image.save(temp_img.name, 'JPEG')
                page = doc.new_page(width=595, height=842)  # A4
                page.insert_image(page.rect, filename=temp_img.name)
        output = io.BytesIO()
        doc.save(output); doc.close()
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        st.error(f"Error converting images to PDF: {e}")
        return None


def text_to_pdf(text_content, title="Document"):
    try:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), text_content, fontsize=12, color=(0, 0, 0))
        output = io.BytesIO()
        doc.save(output); doc.close()
        output.seek(0)
        return output.getvalue()
    except Exception as e:
        st.error(f"Error converting text to PDF: {e}")
        return None


# ---------- FROM PDF ----------

def pdf_to_images(pdf_file, format='PNG', dpi=150, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            images = []
            for page_num in range(doc.page_count):
                page = doc[page_num]
                mat = fitz.Matrix(dpi/72, dpi/72)
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes(format.lower())
                images.append((f"page_{page_num+1}.{format.lower()}", img_data))
            doc.close()
            return images
    except Exception as e:
        st.error(f"Error converting PDF to images: {e}")
        return []


def pdf_to_text(pdf_file, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            all_text = ""
            for page in doc:
                all_text += page.get_text() + "\n\n"
            doc.close()
            return all_text
    except Exception as e:
        st.error(f"Error extracting text from PDF: {e}")
        return None


def convert_pdf_to_docx(file):
    if not PDF2DOCX_AVAILABLE:
        st.error("pdf2docx is not available. Please install pdf2docx.")
        return None, None
    output = io.BytesIO()
    with TMP.create(".pdf") as temp_pdf:
        temp_pdf.write(file.read())
        temp_pdf_path = temp_pdf.name
    with TMP.create(".docx") as temp_docx:
        temp_docx_path = temp_docx.name
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text("Starting conversion...")
    time.sleep(0.2); progress_bar.progress(20)
    try:
        cv = Converter(temp_pdf_path)
        status_text.text("Converting to DOCX...")
        cv.convert(temp_docx_path, start=0, end=None)
        cv.close(); progress_bar.progress(80)
        with open(temp_docx_path, "rb") as f:
            output.write(f.read())
        output.seek(0)
        progress_bar.progress(100); status_text.text("Conversion complete!")
        time.sleep(0.5); progress_bar.empty(); status_text.empty()
        return output.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    except Exception as e:
        st.error(f"Error converting PDF to DOCX: {e}")
        return None, None


# ---------- PDF EDITING FUNCTIONS ----------

def rotate_pdf(pdf_file, rotation_angle, pages=None, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            if pages is None:
                pages = list(range(doc.page_count))
            for page_num in pages:
                if 0 <= page_num < doc.page_count:
                    doc[page_num].set_rotation(rotation_angle)
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error rotating PDF: {e}")
        return None


def add_watermark(pdf_file, watermark_text, position="center", opacity=0.3, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            for page in doc:
                rect = page.rect
                if position == "center":
                    point = fitz.Point(rect.width/2, rect.height/2)
                elif position == "top-left":
                    point = fitz.Point(50, 50)
                elif position == "top-right":
                    point = fitz.Point(rect.width-100, 50)
                elif position == "bottom-left":
                    point = fitz.Point(50, rect.height-50)
                else:
                    point = fitz.Point(rect.width-100, rect.height-50)
                page.insert_text(point, watermark_text, fontsize=40, color=(0.7, 0.7, 0.7), rotate=45)
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error adding watermark: {e}")
        return None


def add_page_numbers(pdf_file, position="bottom-right", start_num=1, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            for page_num, page in enumerate(doc):
                rect = page.rect
                page_number = start_num + page_num
                if position == "bottom-right":
                    point = fitz.Point(rect.width-50, rect.height-20)
                elif position == "bottom-left":
                    point = fitz.Point(30, rect.height-20)
                elif position == "top-right":
                    point = fitz.Point(rect.width-50, 30)
                else:
                    point = fitz.Point(30, 30)
                page.insert_text(point, str(page_number), fontsize=12, color=(0, 0, 0))
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error adding page numbers: {e}")
        return None


def crop_pdf(pdf_file, crop_box, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            for page in doc:
                page.set_cropbox(fitz.Rect(crop_box))
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error cropping PDF: {e}")
        return None


# ---------- PDF SECURITY FUNCTIONS ----------

def protect_pdf(pdf_file, user_password, owner_password=None, password=None):
    try:
        reader = PdfReader(pdf_file)
        if reader.is_encrypted and password:
            reader.decrypt(password)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(
            user_password=user_password,
            owner_password=owner_password or user_password,
            use_128bit=True
        )
        output = io.BytesIO()
        writer.write(output); output.seek(0)
        return output.getvalue()
    except Exception as e:
        st.error(f"Error protecting PDF: {e}")
        return None


def unlock_pdf(pdf_file, password):
    try:
        reader = PdfReader(pdf_file)
        if reader.is_encrypted:
            if reader.decrypt(password) == 0:
                st.error("Incorrect password")
                return None
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        output = io.BytesIO()
        writer.write(output); output.seek(0)
        return output.getvalue()
    except Exception as e:
        st.error(f"Error unlocking PDF: {e}")
        return None


def redact_pdf(pdf_file, redaction_areas, password=None):
    try:
        with TMP.create('.pdf') as temp_file:
            temp_file.write(pdf_file.read()); temp_file.flush()
            doc = fitz.open(temp_file.name)
            if doc.needs_pass and password:
                doc.authenticate(password)
            for page_num, areas in redaction_areas.items():
                if page_num < doc.page_count:
                    page = doc[page_num]
                    for area in areas:
                        rect = fitz.Rect(area)
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                    page.apply_redactions()
            output = io.BytesIO()
            doc.save(output); doc.close()
            output.seek(0)
            return output.getvalue()
    except Exception as e:
        st.error(f"Error redacting PDF: {e}")
        return None


# ---------- COMPARISON FUNCTIONS ----------

def compare_pdfs(pdf1, pdf2, password1=None, password2=None):
    try:
        text1 = pdf_to_text(pdf1, password1)
        text2 = pdf_to_text(pdf2, password2)
        if text1 is None or text2 is None:
            return None
        lines1 = text1.split('\n')
        lines2 = text2.split('\n')
        differences = []
        max_lines = max(len(lines1), len(lines2))
        for i in range(max_lines):
            line1 = lines1[i] if i < len(lines1) else ""
            line2 = lines2[i] if i < len(lines2) else ""
            if line1 != line2:
                differences.append({'line': i + 1, 'pdf1': line1, 'pdf2': line2})
        return differences
    except Exception as e:
        st.error(f"Error comparing PDFs: {e}")
        return None


# ---------- MAIN STREAMLIT APP ----------

def main():
    st.markdown("""
    <div class="main-header">
        <h1>📄 Comprehensive PDF Toolkit</h1>
        <p>Your all-in-one solution for PDF processing, conversion, and editing</p>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.title("🛠️ PDF Tools")

    # Manual cleanup (temps + uploads) via callback (Streamlit-safe)
    st.sidebar.markdown("---")

    def _clear_everything():
        TMP.cleanup()
        UP.clear_all()
        st.session_state["__just_cleared__"] = True

    st.sidebar.button("🧹 Clear Temporary Files Now", on_click=_clear_everything)

    # one-time toast after callback
    if st.session_state.pop("__just_cleared__", False):
        st.sidebar.success("All temporary files and uploads cleared.")

    tool_categories = {
        "📁 Organize": [
            "Merge PDFs", "Split PDF", "Remove Pages", "Extract Pages", "Organize Pages"
        ],
        "🔄 Convert To PDF": [
            "Images to PDF", "Text to PDF"
        ],
        "📤 Convert From PDF": [
            "PDF to Images", "PDF to Word", "PDF to Text", "PDF to Excel"
        ],
        "✏️ Edit PDF": [
            "Rotate PDF", "Add Watermark", "Add Page Numbers", "Crop PDF"
        ],
        "🔒 Security": [
            "Protect PDF", "Unlock PDF", "Redact PDF"
        ],
        "⚡ Optimize": [
            "Compress PDF", "Optimize PDF", "OCR PDF", "Repair PDF"
        ],
        "🔍 Other": [
            "Compare PDFs", "PDF Info"
        ]
    }

    selected_category = st.sidebar.selectbox("Select Category", list(tool_categories.keys()))
    selected_tool = st.sidebar.selectbox("Select Tool", tool_categories[selected_category])

    # Helper to create uploaders that are tracked
    def tracked_uploader(label, key, type, accept_multiple_files=False):
        UP.register(key, accept_multiple_files)
        return st.file_uploader(label, type=type, accept_multiple_files=accept_multiple_files, key=key)

    if selected_tool == "Merge PDFs":
        st.header("🧩 Merge PDF Files")
        uploaded_pdfs = tracked_uploader("Upload multiple PDFs", "u_merge_files", ["pdf"], True)

        if uploaded_pdfs:
            filenames = [f.name for f in uploaded_pdfs]
            if "file_order" not in st.session_state or len(st.session_state.file_order) != len(filenames):
                st.session_state.file_order = list(range(len(filenames)))
            file_order = st.session_state.file_order

            st.subheader("📚 Arrange PDFs")
            for i, idx in enumerate(file_order):
                col1, col2, col3 = st.columns([4, 1, 1])
                with col1:
                    st.write(f"{i+1}. {filenames[idx]}")
                with col2:
                    if i > 0 and st.button("⬆️", key=f"up_{i}"):
                        file_order[i], file_order[i-1] = file_order[i-1], file_order[i]
                        st.session_state.file_order = file_order
                        st.rerun()
                with col3:
                    if i < len(file_order) - 1 and st.button("⬇️", key=f"down_{i}"):
                        file_order[i], file_order[i+1] = file_order[i+1], file_order[i]
                        st.session_state.file_order = file_order
                        st.rerun()

            st.subheader("🔐 Passwords for Encrypted PDFs")
            passwords = []
            for i, f in enumerate(uploaded_pdfs):
                pw = st.text_input(
                    f"Enter password for file #{i+1}: {f.name}",
                    type="password",
                    key=f"pw_{i}"  # unique per index
                )
                passwords.append(pw)

            if st.button("Merge PDFs"):
                with st.spinner("Merging PDFs..."):
                    merged_pdf = merge_pdfs(uploaded_pdfs, file_order, passwords)
                    if merged_pdf:
                        st.success("PDFs merged successfully!")
                        clicked = st.download_button("📥 Download Merged PDF", merged_pdf, file_name="merged.pdf", mime="application/pdf", key="dl_merged_pdf")
                        if clicked:
                            TMP.cleanup()
                            st.info("Temporary files removed.")

    elif selected_tool == "Split PDF":
        st.header("✂️ Split PDF into Separate Files")
        pdf_file = tracked_uploader("Upload PDF to Split", "u_split_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            # Safely read basic info
            num_pages = None
            try:
                pdf_file.seek(0)
                reader = PdfReader(pdf_file)
                if reader.is_encrypted and password:
                    reader.decrypt(password)
                num_pages = len(reader.pages)
            except Exception as e:
                st.error(f"Unable to read PDF: {e}")

            if num_pages:
                st.info(f"PDF has {num_pages} pages.")
                pdf_file.seek(0)
                preview = get_pdf_preview(pdf_file, 0, password)
                if preview:
                    st.image(preview, caption="First page preview", width=300)

                st.subheader("Page Splitting Options")
                auto_split = st.checkbox("🔁 Auto-split in equal groups")
                if auto_split:
                    group_size = st.number_input("Pages per group", min_value=1, max_value=num_pages, value=2, step=1)
                    range_input = None
                else:
                    range_input = st.text_input("Enter page ranges (e.g., 1-2,3-4,5-6)")

                if st.button("Split PDF", type="primary"):
                    try:
                        ranges = []
                        if auto_split:
                            for start in range(1, num_pages + 1, group_size):
                                end = min(start + group_size - 1, num_pages)
                                ranges.append((start, end))
                        else:
                            for part in range_input.split(','):
                                if '-' in part:
                                    start, end = map(int, part.strip().split('-'))
                                else:
                                    start = end = int(part.strip())
                                if start <= end <= num_pages:
                                    ranges.append((start, end))
                                else:
                                    st.warning(f"Ignored invalid range: {part}")

                        if ranges:
                            pdf_file.seek(0)
                            split_files = split_pdf(pdf_file, ranges, password)

                            for name, content in split_files:
                                clicked_each = st.download_button(f"📥 Download {name}", content, file_name=name, mime="application/pdf", key=f"dl_split_{name}")
                                if clicked_each:
                                    TMP.cleanup()
                                    st.info("Temporary files removed.")

                            zip_buffer = io.BytesIO()
                            with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
                                for name, content in split_files:
                                    zip_file.writestr(name, content)
                            zip_buffer.seek(0)

                            clicked_zip = st.download_button("📦 Download All as ZIP", zip_buffer.getvalue(),
                                                             file_name="split_files.zip", mime="application/zip", key="dl_split_zip")
                            if clicked_zip:
                                TMP.cleanup()
                                st.info("Temporary files removed.")
                    except Exception as e:
                        st.error(f"Error processing split: {e}")

    elif selected_tool == "Compress PDF":
        st.header("🗜️ Compress PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_compress_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        quality = st.select_slider("Compression Quality",
                                   options=["High Quality", "Medium Quality", "High Compression"],
                                   value="Medium Quality")
        quality_map = {"High Quality": "high", "Medium Quality": "medium", "High Compression": "low"}

        if pdf_file and st.button("Compress PDF", type="primary"):
            with st.spinner("Compressing PDF..."):
                pdf_file.seek(0); original_size = len(pdf_file.read()); pdf_file.seek(0)
                compressed_pdf = compress_pdf(pdf_file, quality_map[quality], password)
                if compressed_pdf:
                    compressed_size = len(compressed_pdf)
                    compression_ratio = ((original_size - compressed_size) / original_size) * 100
                    col1, col2, col3 = st.columns(3)
                    with col1: st.metric("Original Size", f"{original_size / 1024 / 1024:.2f} MB")
                    with col2: st.metric("Compressed Size", f"{compressed_size / 1024 / 1024:.2f} MB")
                    with col3: st.metric("Space Saved", f"{compression_ratio:.1f}%")
                    st.success("PDF compressed successfully!")
                    clicked = st.download_button("📥 Download Compressed PDF", compressed_pdf,
                                                 file_name="compressed.pdf", mime="application/pdf", key="dl_compress")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "PDF to Word":
        st.header("📄 Convert PDF to Word")
        pdf_file = tracked_uploader("Upload PDF file", "u_pdf2word_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Pages", info['pages']); st.metric("Title", info['title'])
                with col2:
                    st.metric("Author", info['author']); st.metric("Creator", info['creator'])
            pdf_file.seek(0)
            preview = get_pdf_preview(pdf_file, 0, password)
            if preview:
                st.image(preview, caption="First page preview", width=400)

            if st.button("Convert to Word", type="primary"):
                pdf_file.seek(0)
                with st.spinner("Converting..."):
                    try:
                        docx_bytes, mime = convert_pdf_to_docx(pdf_file)
                        if docx_bytes:
                            st.success("Conversion completed!")
                            clicked = st.download_button("📥 Download Word Document", docx_bytes,
                                                         file_name=pdf_file.name.replace(".pdf", ".docx"), mime=mime, key="dl_docx")
                            if clicked:
                                TMP.cleanup()
                                st.info("Temporary files removed.")
                    except Exception as e:
                        st.error(f"Conversion failed: {e}")

    elif selected_tool == "Images to PDF":
        st.header("🖼️ Convert Images to PDF")
        image_files = tracked_uploader("Upload images", "u_images_to_pdf", ["jpg", "jpeg", "png", "bmp"], True)

        if image_files:
            st.subheader("Preview Images")
            cols = st.columns(min(3, len(image_files)))
            for i, img in enumerate(image_files):
                with cols[i % 3]:
                    st.image(img, caption=img.name, width=150)

            if st.button("Convert to PDF", type="primary"):
                with st.spinner("Converting images to PDF..."):
                    pdf_bytes = images_to_pdf(image_files)
                    if pdf_bytes:
                        st.success("Images converted to PDF!")
                        clicked = st.download_button("📥 Download PDF", pdf_bytes, file_name="images.pdf", mime="application/pdf", key="dl_img_pdf")
                        if clicked:
                            TMP.cleanup()
                            st.info("Temporary files removed.")

    elif selected_tool == "PDF to Images":
        st.header("🖼️ Convert PDF to Images")
        pdf_file = tracked_uploader("Upload PDF file", "u_pdf2images_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        col1, col2 = st.columns(2)
        with col1:
            image_format = st.selectbox("Image Format", ["PNG", "JPEG"])
        with col2:
            dpi = st.slider("DPI (Quality)", 72, 300, 150)

        if pdf_file and st.button("Convert to Images", type="primary"):
            with st.spinner("Converting PDF to images..."):
                pdf_file.seek(0)
                images = pdf_to_images(pdf_file, image_format, dpi, password)
                if images:
                    st.success(f"Converted {len(images)} pages to images!")
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
                        for name, img_data in images:
                            zip_file.writestr(name, img_data)
                    zip_buffer.seek(0)
                    clicked = st.download_button("📥 Download All Images (ZIP)", zip_buffer.getvalue(),
                                                 file_name="pdf_images.zip", mime="application/zip", key="dl_pdf_images_zip")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "PDF to Excel":
        st.header("📊 Convert PDF to Excel")
        pdf_file = tracked_uploader("Upload PDF file", "u_pdf2excel_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        col1, col2 = st.columns(2)
        with col1:
            extraction_method = st.selectbox("Extraction Method", ["tables", "text"],
                                             help="Tables: Extract structured tables\nText: Parse text as potential tables")
        with col2:
            if pdf_file:
                pdf_file.seek(0)
                info = get_pdf_info(pdf_file)
                if info:
                    st.metric("Pages", info['pages'])

        if pdf_file:
            pdf_file.seek(0)
            preview = get_pdf_preview(pdf_file, 0, password)
            if preview:
                st.image(preview, caption="First page preview", width=400)

            if st.button("Convert to Excel", type="primary"):
                with st.spinner("Converting PDF to Excel..."):
                    pdf_file.seek(0)
                    excel_bytes = pdf_to_excel(pdf_file, password, extraction_method)
                    if excel_bytes:
                        st.success("PDF converted to Excel successfully!")
                        clicked = st.download_button("📥 Download Excel File", excel_bytes,
                                                     file_name=pdf_file.name.replace(".pdf", ".xlsx"),
                                                     mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                                     key="dl_excel")
                        if clicked:
                            TMP.cleanup()
                            st.info("Temporary files removed.")

    elif selected_tool == "Add Watermark":
        st.header("💧 Add Watermark to PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_watermark_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        col1, col2 = st.columns(2)
        with col1:
            watermark_text = st.text_input("Watermark Text", "CONFIDENTIAL")
            position = st.selectbox("Position", ["center", "top-left", "top-right", "bottom-left", "bottom-right"])
        with col2:
            opacity = st.slider("Opacity", 0.1, 1.0, 0.3)

        if pdf_file and st.button("Add Watermark", type="primary"):
            with st.spinner("Adding watermark..."):
                pdf_file.seek(0)
                watermarked_pdf = add_watermark(pdf_file, watermark_text, position, opacity, password)
                if watermarked_pdf:
                    st.success("Watermark added successfully!")
                    clicked = st.download_button("📥 Download Watermarked PDF", watermarked_pdf,
                                                 file_name="watermarked.pdf", mime="application/pdf", key="dl_watermark")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Protect PDF":
        st.header("🔒 Protect PDF with Password")
        pdf_file = tracked_uploader("Upload PDF file", "u_protect_pdf", ["pdf"])
        current_password = st.text_input("Current Password (if encrypted)", type="password")

        col1, col2 = st.columns(2)
        with col1:
            user_password = st.text_input("New User Password", type="password")
        with col2:
            owner_password = st.text_input("Owner Password (optional)", type="password")

        if pdf_file and user_password and st.button("Protect PDF", type="primary"):
            with st.spinner("Adding password protection..."):
                pdf_file.seek(0)
                protected_pdf = protect_pdf(pdf_file, user_password, owner_password, current_password)
                if protected_pdf:
                    st.success("PDF protected successfully!")
                    clicked = st.download_button("📥 Download Protected PDF", protected_pdf,
                                                 file_name="protected.pdf", mime="application/pdf", key="dl_protect")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Unlock PDF":
        st.header("🔓 Remove Password Protection")
        pdf_file = tracked_uploader("Upload encrypted PDF file", "u_unlock_pdf", ["pdf"])
        password = st.text_input("Enter PDF Password", type="password")

        if pdf_file and password and st.button("Unlock PDF", type="primary"):
            with st.spinner("Removing password protection..."):
                pdf_file.seek(0)
                unlocked_pdf = unlock_pdf(pdf_file, password)
                if unlocked_pdf:
                    st.success("PDF unlocked successfully!")
                    clicked = st.download_button("📥 Download Unlocked PDF", unlocked_pdf,
                                                 file_name="unlocked.pdf", mime="application/pdf", key="dl_unlock")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "OCR PDF":
        st.header("🔍 OCR - Make PDF Searchable")
        pdf_file = tracked_uploader("Upload PDF file", "u_ocr_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")
        language = st.selectbox("OCR Language", ["eng", "spa", "fra", "deu", "ita", "por"])

        if pdf_file and st.button("Perform OCR", type="primary"):
            with st.spinner("Performing OCR... This may take a while."):
                pdf_file.seek(0)
                ocr_pdf_result = ocr_pdf(pdf_file, language, password)
                if ocr_pdf_result:
                    st.success("OCR completed successfully!")
                    clicked = st.download_button("📥 Download Searchable PDF", ocr_pdf_result,
                                                 file_name="ocr.pdf", mime="application/pdf", key="dl_ocr")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Add Page Numbers":
        st.header("🔢 Add Page Numbers")
        pdf_file = tracked_uploader("Upload PDF file", "u_pagenum_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        col1, col2 = st.columns(2)
        with col1:
            position = st.selectbox("Position", ["bottom-right", "bottom-left", "top-right", "top-left"])
        with col2:
            start_num = st.number_input("Starting Number", min_value=1, value=1)

        if pdf_file and st.button("Add Page Numbers", type="primary"):
            with st.spinner("Adding page numbers..."):
                pdf_file.seek(0)
                numbered_pdf = add_page_numbers(pdf_file, position, start_num, password)
                if numbered_pdf:
                    st.success("Page numbers added successfully!")
                    clicked = st.download_button("📥 Download Numbered PDF", numbered_pdf,
                                                 file_name="numbered.pdf", mime="application/pdf", key="dl_numbers")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Rotate PDF":
        st.header("🔄 Rotate PDF Pages")
        pdf_file = tracked_uploader("Upload PDF file", "u_rotate_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")
        rotation_angle = st.selectbox("Rotation Angle", [90, 180, 270])

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.info(f"PDF has {info['pages']} pages")
                page_option = st.radio("Rotate", ["All pages", "Specific pages"])
                pages_to_rotate = None
                if page_option == "Specific pages":
                    page_input = st.text_input("Page numbers (e.g., 1,3,5 or 1-3)")
                    if page_input:
                        try:
                            pages_to_rotate = []
                            for part in page_input.split(','):
                                if '-' in part:
                                    start, end = map(int, part.strip().split('-'))
                                    pages_to_rotate.extend(range(start-1, end))
                                else:
                                    pages_to_rotate.append(int(part.strip())-1)
                        except:
                            st.error("Invalid page format")

                if st.button("Rotate PDF", type="primary"):
                    with st.spinner("Rotating PDF..."):
                        pdf_file.seek(0)
                        rotated_pdf = rotate_pdf(pdf_file, rotation_angle, pages_to_rotate, password)
                        if rotated_pdf:
                            st.success("PDF rotated successfully!")
                            clicked = st.download_button("📥 Download Rotated PDF", rotated_pdf,
                                                         file_name="rotated.pdf", mime="application/pdf", key="dl_rotate")
                            if clicked:
                                TMP.cleanup()
                                st.info("Temporary files removed.")

    elif selected_tool == "Remove Pages":
        st.header("🗑️ Remove Pages from PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_remove_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.info(f"PDF has {info['pages']} pages")
                pages_to_remove_input = st.text_input("Pages to remove (e.g., 1,3,5 or 1-3)")
                if pages_to_remove_input and st.button("Remove Pages", type="primary"):
                    try:
                        pages_to_remove = []
                        for part in pages_to_remove_input.split(','):
                            if '-' in part:
                                start, end = map(int, part.strip().split('-'))
                                pages_to_remove.extend(range(start-1, end))
                            else:
                                pages_to_remove.append(int(part.strip())-1)
                        with st.spinner("Removing pages..."):
                            pdf_file.seek(0)
                            result_pdf = remove_pdf_pages(pdf_file, pages_to_remove, password)
                            if result_pdf:
                                st.success("Pages removed successfully!")
                                clicked = st.download_button("📥 Download Modified PDF", result_pdf,
                                                             file_name="pages_removed.pdf", mime="application/pdf", key="dl_remove")
                                if clicked:
                                    TMP.cleanup()
                                    st.info("Temporary files removed.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    elif selected_tool == "Extract Pages":
        st.header("📋 Extract Pages from PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_extract_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.info(f"PDF has {info['pages']} pages")
                pages_to_extract_input = st.text_input("Pages to extract (e.g., 1,3,5 or 1-3)")
                if pages_to_extract_input and st.button("Extract Pages", type="primary"):
                    try:
                        pages_to_extract = []
                        for part in pages_to_extract_input.split(','):
                            if '-' in part:
                                start, end = map(int, part.strip().split('-'))
                                pages_to_extract.extend(range(start-1, end))
                            else:
                                pages_to_extract.append(int(part.strip())-1)
                        with st.spinner("Extracting pages..."):
                            pdf_file.seek(0)
                            result_pdf = extract_pdf_pages(pdf_file, pages_to_extract, password)
                            if result_pdf:
                                st.success("Pages extracted successfully!")
                                clicked = st.download_button("📥 Download Extracted PDF", result_pdf,
                                                             file_name="extracted_pages.pdf", mime="application/pdf", key="dl_extract")
                                if clicked:
                                    TMP.cleanup()
                                    st.info("Temporary files removed.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    elif selected_tool == "Text to PDF":
        st.header("📝 Convert Text to PDF")
        input_method = st.radio("Input Method", ["Type Text", "Upload Text File"])
        if input_method == "Type Text":
            text_content = st.text_area("Enter text content", height=200, value="Enter your text here...")
        else:
            text_file = tracked_uploader("Upload text file", "u_text_file", ["txt"])
            if text_file:
                text_content = text_file.read().decode('utf-8')
            else:
                text_content = ""
        if text_content and st.button("Convert to PDF", type="primary"):
            with st.spinner("Converting text to PDF..."):
                pdf_bytes = text_to_pdf(text_content)
                if pdf_bytes:
                    st.success("Text converted to PDF!")
                    clicked = st.download_button("📥 Download PDF", pdf_bytes,
                                                 file_name="text_document.pdf", mime="application/pdf", key="dl_text_pdf")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "PDF to Text":
        st.header("📝 Extract Text from PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_pdf2text_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file and st.button("Extract Text", type="primary"):
            with st.spinner("Extracting text..."):
                pdf_file.seek(0)
                extracted_text = pdf_to_text(pdf_file, password)
                if extracted_text:
                    st.success("Text extracted successfully!")
                    st.text_area("Extracted Text", extracted_text, height=300)
                    clicked = st.download_button("📥 Download Text File", extracted_text.encode(),
                                                 file_name=pdf_file.name.replace(".pdf", ".txt"),
                                                 mime="text/plain", key="dl_text")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Compare PDFs":
        st.header("🔍 Compare Two PDFs")
        col1, col2 = st.columns(2)
        with col1:
            pdf1 = tracked_uploader("Upload first PDF", "pdf1", ["pdf"])
            password1 = st.text_input("Password for first PDF", type="password", key="pass1")
        with col2:
            pdf2 = tracked_uploader("Upload second PDF", "pdf2", ["pdf"])
            password2 = st.text_input("Password for second PDF", type="password", key="pass2")

        if pdf1 and pdf2 and st.button("Compare PDFs", type="primary"):
            with st.spinner("Comparing PDFs..."):
                pdf1.seek(0); pdf2.seek(0)
                differences = compare_pdfs(pdf1, pdf2, password1, password2)
                if differences is not None:
                    if differences:
                        st.warning(f"Found {len(differences)} differences:")
                        for diff in differences[:10]:
                            st.write(f"**Line {diff['line']}:**")
                            st.write(f"PDF 1: {diff['pdf1']}")
                            st.write(f"PDF 2: {diff['pdf2']}")
                            st.write("---")
                    else:
                        st.success("PDFs are identical!")

    elif selected_tool == "PDF Info":
        st.header("ℹ️ PDF Information")
        pdf_file = tracked_uploader("Upload PDF file", "u_pdfinfo_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.markdown("### 📋 PDF Details")
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("📄 Pages", info['pages'])
                    st.metric("👤 Author", info['author'])
                    st.metric("🏷️ Title", info['title'])
                with col2:
                    st.metric("🛠️ Creator", info['creator'])
                    st.metric("📅 Created", info['creation_date'])
                    st.metric("📅 Modified", info['modification_date'])
                pdf_file.seek(0)
                preview = get_pdf_preview(pdf_file, 0, password)
                if preview:
                    st.markdown("### 👀 Preview")
                    st.image(preview, caption="First page", width=400)

    elif selected_tool == "Optimize PDF":
        st.header("⚡ Optimize PDF")
        pdf_file = tracked_uploader("Upload PDF file", "u_optimize_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file and st.button("Optimize PDF", type="primary"):
            with st.spinner("Optimizing PDF..."):
                pdf_file.seek(0); original_size = len(pdf_file.read()); pdf_file.seek(0)
                optimized_pdf = optimize_pdf(pdf_file, password)
                if optimized_pdf:
                    optimized_size = len(optimized_pdf)
                    optimization_ratio = ((original_size - optimized_size) / original_size) * 100
                    col1, col2, col3 = st.columns(3)
                    with col1: st.metric("Original Size", f"{original_size / 1024 / 1024:.2f} MB")
                    with col2: st.metric("Optimized Size", f"{optimized_size / 1024 / 1024:.2f} MB")
                    with col3: st.metric("Space Saved", f"{optimization_ratio:.1f}%")
                    st.success("PDF optimized successfully!")
                    clicked = st.download_button("📥 Download Optimized PDF", optimized_pdf,
                                                 file_name="optimized.pdf", mime="application/pdf", key="dl_optimize")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Repair PDF":
        st.header("🔧 Repair Corrupted PDF")
        pdf_file = tracked_uploader("Upload corrupted PDF file", "u_repair_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file and st.button("Repair PDF", type="primary"):
            with st.spinner("Repairing PDF..."):
                pdf_file.seek(0)
                repaired_pdf = repair_pdf(pdf_file, password)
                if repaired_pdf:
                    st.success("PDF repaired successfully!")
                    clicked = st.download_button("📥 Download Repaired PDF", repaired_pdf,
                                                 file_name="repaired.pdf", mime="application/pdf", key="dl_repair")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Crop PDF":
        st.header("✂️ Crop PDF Pages")
        pdf_file = tracked_uploader("Upload PDF file", "u_crop_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        st.subheader("Crop Settings")
        col1, col2 = st.columns(2)
        with col1:
            left = st.number_input("Left margin", min_value=0, value=50)
            top = st.number_input("Top margin", min_value=0, value=50)
        with col2:
            right = st.number_input("Right margin", min_value=0, value=545)
            bottom = st.number_input("Bottom margin", min_value=0, value=792)
        crop_box = [left, top, right, bottom]

        if pdf_file and st.button("Crop PDF", type="primary"):
            with st.spinner("Cropping PDF..."):
                pdf_file.seek(0)
                cropped_pdf = crop_pdf(pdf_file, crop_box, password)
                if cropped_pdf:
                    st.success("PDF cropped successfully!")
                    clicked = st.download_button("📥 Download Cropped PDF", cropped_pdf,
                                                 file_name="cropped.pdf", mime="application/pdf", key="dl_crop")
                    if clicked:
                        TMP.cleanup()
                        st.info("Temporary files removed.")

    elif selected_tool == "Redact PDF":
        st.header("🖍️ Redact Sensitive Information")
        pdf_file = tracked_uploader("Upload PDF file", "u_redact_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")
        st.info("This is a simplified redaction tool. For production use, consider professional redaction software.")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.info(f"PDF has {info['pages']} pages")
                page_num = st.number_input("Page to redact", min_value=1, max_value=info['pages'], value=1) - 1
                st.subheader("Redaction Area (coordinates)")
                col1, col2 = st.columns(2)
                with col1:
                    x1 = st.number_input("X1", min_value=0, value=100)
                    y1 = st.number_input("Y1", min_value=0, value=100)
                with col2:
                    x2 = st.number_input("X2", min_value=0, value=200)
                    y2 = st.number_input("Y2", min_value=0, value=150)
                redaction_areas = {page_num: [[x1, y1, x2, y2]]}

                if st.button("Redact PDF", type="primary"):
                    with st.spinner("Redacting PDF..."):
                        pdf_file.seek(0)
                        redacted_pdf = redact_pdf(pdf_file, redaction_areas, password)
                        if redacted_pdf:
                            st.success("PDF redacted successfully!")
                            clicked = st.download_button("📥 Download Redacted PDF", redacted_pdf,
                                                         file_name="redacted.pdf", mime="application/pdf", key="dl_redact")
                            if clicked:
                                TMP.cleanup()
                                st.info("Temporary files removed.")

    elif selected_tool == "Organize Pages":
        st.header("📑 Organize PDF Pages")
        pdf_file = tracked_uploader("Upload PDF file", "u_organize_pdf", ["pdf"])
        password = st.text_input("Password if encrypted", type="password")

        if pdf_file:
            pdf_file.seek(0)
            info = get_pdf_info(pdf_file)
            if info:
                st.info(f"PDF has {info['pages']} pages")
                st.subheader("New Page Order")
                new_order_input = st.text_input("Enter new page order (e.g., 3,1,2,4)")
                if new_order_input and st.button("Reorganize Pages", type="primary"):
                    try:
                        new_order = [int(x.strip()) - 1 for x in new_order_input.split(',')]
                        if len(new_order) != info['pages'] or any(x < 0 or x >= info['pages'] for x in new_order):
                            st.error("Invalid page order. Please specify all pages exactly once.")
                        else:
                            with st.spinner("Reorganizing pages..."):
                                pdf_file.seek(0)
                                organized_pdf = organize_pdf_pages(pdf_file, new_order, password)
                                if organized_pdf:
                                    st.success("Pages reorganized successfully!")
                                    clicked = st.download_button("📥 Download Organized PDF", organized_pdf,
                                                                 file_name="organized.pdf", mime="application/pdf", key="dl_organize")
                                    if clicked:
                                        TMP.cleanup()
                                        st.info("Temporary files removed.")

                    except Exception as e:
                        st.error(f"Error: {e}")

    else:
        st.header("🚧 Coming Soon")
        st.info(f"The {selected_tool} feature is currently under development. Please check back soon!")

    # Footer
    st.markdown("---")
    st.markdown("""
  <div style="text-align: center; color: #666; padding: 2rem;">
  <p>Comprehensive PDF Toolkit - Your all-in-one PDF solution</p>
  <p>
    Built with ❤️ by 
    <a href="https://nirmalchathura.dev" 
       target="_blank" 
       style="color: #007BFF; text-decoration: none; font-weight: 500; transition: color 0.3s;"
       onmouseover="this.style.color='#0056b3'"
       onmouseout="this.style.color='#007BFF'">
      Nirmal
    </a>
  </p>
</div>


    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
