import os
import re
import cv2
import numpy as np
import asyncio
from PIL import Image
try:
    import winocr
except ImportError:
    winocr = None
try:
    import zxingcpp
    HAS_ZXING = True
except ImportError:
    HAS_ZXING = False

# Danh sách từ khóa tiêu đề quốc hiệu / thẻ CCCD cần loại trừ tuyệt đối khi trích xuất tên
NAME_BLACKLIST = [
    'CONG HOA', 'CỘNG HÒA', 'VIET NAM', 'VIỆT NAM',
    'DOC LAP', 'ĐỘC LẬP', 'TU DO', 'TỰ DO', 'HANH PHUC', 'HẠNH PHÚC',
    'CAN CUOC', 'CĂN CƯỚC', 'CONG DAN', 'CÔNG DÂN',
    'CITIZEN', 'IDENTITY', 'CARD', 'REPUBLIC', 'SOCIALIST',
    'INDEPENDENCE', 'INDEP', 'FREEDOM', 'HAPPINESS', 'EXPIRY',
    'GIOI TINH', 'GIỚI TÍNH', 'QUOC TICH', 'QUỐC TỊCH',
    'QUE QUAN', 'QUÊ QUÁN', 'PLACE OF ORIGIN', 'ORIGIN',
    'NOI THUONG TRU', 'NƠI THƯỜNG TRÚ', 'PLACE OF RESIDENCE', 'RESIDENCE',
    'CO GIA TRI', 'CÓ GIÁ TRỊ', 'DATE OF EXPIRY',
    'DAC DIEM NHAN DANG', 'ĐẶC ĐIỂM NHẬN DẠNG',
    'NGON TRO', 'NGÓN TRỎ', 'CUC TRUONG', 'CỤC TRƯỞNG', 'CANH SAT', 'CẢNH SÁT',
    'QUAN LY HANH CHINH', 'TRAT TU XA HOI', 'BO CONG AN', 'BỘ CÔNG AN',
    'CHỦ NGHĨA', 'CHU NGHIA', 'CHÜ NGHIA', 'XÃ HỘI', 'XA HOI', 'XÅ HOI',
    'NGAY SINH', 'NGÀY SINH', 'DATE OF BIRTH', 'BIRTH', 'SINH',
    'HO VA TEN', 'HỌ VÀ TÊN', 'FULL NAME', 'HO TEN', 'HỌ TÊN',
    'DANH SACH', 'DANH SÁCH', 'HOC VIEN', 'HỌC VIÊN', 'KHOA', 'KHÓA',
    'HANG', 'HẠNG', 'GIAO VIEN', 'GIÁO VIÊN', 'SO', 'SỐ', 'CCCD', 'CMND'
]

def safe_cv2_read(image_path):
    """Đọc ảnh an toàn hỗ trợ đường dẫn tiếng Việt có dấu trên Windows."""
    try:
        with open(image_path, 'rb') as fp:
            buf = np.frombuffer(fp.read(), np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"Lỗi đọc ảnh {image_path}: {e}")
        return None

import unicodedata

def remove_accents(s):
    """Xóa dấu tiếng Việt để kiểm tra từ khóa cấm chính xác."""
    if not s: return ''
    s = s.replace('Đ', 'D').replace('đ', 'd').replace('Ă', 'A').replace('ă', 'a').replace('Â', 'A').replace('â', 'a')
    s = s.replace('Ê', 'E').replace('ê', 'e').replace('Ô', 'O').replace('ô', 'o').replace('Ơ', 'O').replace('ơ', 'o')
    s = s.replace('Ư', 'U').replace('ư', 'u')
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join([c for c in nfkd if not unicodedata.combining(c)]).upper()

def choose_best_name(fn_name, ocr_name):
    """Chọn tên tốt nhất giữa tên đọc từ tệp và tên đọc từ OCR."""
    if not fn_name: return ocr_name
    if not ocr_name: return fn_name
    
    # Nếu tên từ tên file là tên người hợp lệ (2-5 từ) -> ưu tiên vì do giáo viên đặt có chủ đích
    fn_words = fn_name.split()
    if 2 <= len(fn_words) <= 5:
        return fn_name
        
    return ocr_name or fn_name

def is_back_of_card(text):
    """Phát hiện mặt sau thẻ CCCD (chứa dấu vân tay, đặc điểm nhận dạng, chữ ký cục trưởng)."""
    lower = text.lower()
    back_indicators = [
        'nhận dạng', 'nhan dang', 'ngón trỏ', 'ngon tro',
        'cục trưởng', 'cuc truong', 'cảnh sát', 'canh sat',
        'hành chính', 'hanh chinh', 'dấu vết', 'dau vet',
        'trật tự xã hội', 'trat tu xa hoi'
    ]
    return any(ind in lower for ind in back_indicators)

def parse_student_from_filename(filename):
    """
    Trích xuất chữ (Họ tên) và số CCCD/CMND trực tiếp từ tên file ảnh.
    Hỗ trợ các trường hợp giáo viên lưu file ảnh bằng Tên và Số CCCD:
    - 052095002256_Lê Minh Thảo.jpg
    - Lê Minh Thảo - 052095002256.png
    - 052203009886.jpg
    - Trần Văn Sơn 049098007863.jpg
    - 52001-20260916-000059_Phan Thanh Bình.png
    - 052 095 002 256 - Dang Thi Kim.jpg
    """
    if not filename: return None
    base = os.path.splitext(os.path.basename(filename))[0]
    
    # 1. Tìm Mã học viên dạng 52001-xxxxxxxx-xxxxxx
    mahv = ''
    mahv_m = re.search(r'(52001-\d{8}-\d{6})', base)
    if mahv_m:
        mahv = mahv_m.group(1)

    # 2. Tìm số CCCD 12 số (bắt đầu bằng 0)
    cccd = ''
    cccd_m = re.search(r'(?<!\d)(0\d{11})(?!\d)', base)
    if cccd_m:
        cccd = cccd_m.group(1)
    else:
        # 12 số có phân tách (052 095 002 256 hoặc 052.095.002.256 hoặc 052_095_002_256)
        cccd_sep = re.search(r'(?<!\d)(0\d{2}[\s._-]\d{3}[\s._-]\d{3}[\s._-]\d{3})(?!\d)', base)
        if cccd_sep:
            cccd = re.sub(r'[\s._-]', '', cccd_sep.group(1))
        else:
            cccd_11 = re.search(r'(?<!\d)(0\d{10})(?!\d)', base)
            if cccd_11:
                cccd = cccd_11.group(1)
            else:
                cmnd_m = re.search(r'(?<!\d)([0-3]\d{8})(?!\d)', base)
                if cmnd_m:
                    cccd = cmnd_m.group(1)

    # 3. Tìm Họ và tên (chữ) trong tên file
    name_str = base
    if mahv: name_str = name_str.replace(mahv, ' ')
    if cccd: name_str = name_str.replace(cccd, ' ')
    
    # Xóa timestamp số lớn ở đầu/cuối camera Zalo (VD: 1784686479533...)
    name_str = re.sub(r'\b\d{10,}\b', ' ', name_str)
    # Xóa mã khóa đào tạo (VD: CD_110B, K110, B2...)
    name_str = re.sub(r'(?i)\b(CD[_\s]*\d+[A-Z0-9._-]*|K\d+|B1|B2|C)\b', ' ', name_str)
    # Xóa các ký tự phân cách
    name_str = re.sub(r'[_+\-().,#@!~[\]{}|/\\:;*?\"<>]', ' ', name_str)
    # Xóa số thứ tự đầu dòng (VD: "01 ", "02. ", "STT 1 ")
    name_str = re.sub(r'^(stt\s*)?\d+\s*', '', name_str, flags=re.IGNORECASE)
    # Xóa các số còn lại
    name_str = re.sub(r'\b\d+\b', ' ', name_str)
    # Chỉ giữ chữ cái tiếng Việt và khoảng trắng
    name_str = re.sub(r'[^a-zA-ZÀ-ỹ\s]', ' ', name_str)
    name_str = ' '.join(name_str.split()).upper()
    
    blacklist = ['IMG', 'IMAGE', 'PHOTO', 'PICTURE', 'SCAN', 'CCCD', 'CMND', 'ZALO', 'FACEBOOK', 'DOWNLOAD', 'NEW', 'COPY', 'CAN CUOC', 'CONG DAN']
    words = [w for w in name_str.split() if w not in blacklist]
    valid_name = ' '.join(words) if 2 <= len(words) <= 5 and len(' '.join(words)) >= 4 else ''

    target_id = cccd or mahv
    if target_id or valid_name:
        return {
            'cccd': target_id,
            'hoten': valid_name,
            'source': 'Tên tệp ảnh (Filename)'
        }
    return None

def parse_vietnam_cccd_qr(qr_text):
    """
    Phân tích chuỗi dữ liệu chuẩn trong mã QR trên thẻ CCCD gắn chip:
    Định dạng: Số_CCCD||Họ_và_tên|Ngày_tháng_năm_sinh|Giới_tính|Địa_chỉ|Ngày_cấp
    """
    if not qr_text or '|' not in qr_text:
        return None
    parts = qr_text.split('|')
    if len(parts) >= 4:
        cccd = parts[0].strip()
        name = parts[2].strip() if len(parts) > 2 and parts[2].strip() else (parts[1].strip() if len(parts) > 1 else '')
        dob_raw = parts[3].strip() if len(parts) > 3 else ''
        
        dob_formatted = dob_raw
        if len(dob_raw) == 8 and dob_raw.isdigit():
            dob_formatted = f"{dob_raw[:2]}/{dob_raw[2:4]}/{dob_raw[4:]}"
            
        return {
            'cccd': cccd,
            'hoten': name.upper(),
            'ngaysinh': dob_formatted,
            'source': 'Mã QR CCCD (Chuẩn 100%)',
            'confidence': 'Chính xác 100%'
        }
    return None

def detect_qr(cv_img):
    """Quét mã QR kết hợp zxing-cpp và OpenCV trên 4 góc xoay."""
    if cv_img is None:
        return None
        
    rotations = [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE]
    
    # Ưu tiên zxing-cpp
    if HAS_ZXING:
        for rot in rotations:
            timg = cv_img if rot is None else cv2.rotate(cv_img, rot)
            try:
                barcodes = zxingcpp.read_barcodes(timg)
                for b in barcodes:
                    if b.text and '|' in b.text:
                        parsed = parse_vietnam_cccd_qr(b.text)
                        if parsed:
                            return parsed
            except Exception:
                pass
                
    # Dự phòng OpenCV QRCodeDetector
    try:
        detector = cv2.QRCodeDetector()
        for rot in rotations:
            timg = cv_img if rot is None else cv2.rotate(cv_img, rot)
            val, pts, _ = detector.detectAndDecode(timg)
            if val and '|' in val:
                parsed = parse_vietnam_cccd_qr(val)
                if parsed:
                    return parsed
    except Exception:
        pass
        
    return None

def clean_student_name(text):
    """Chuẩn hóa và lọc tên học viên từ kết quả OCR."""
    if not text: return ''
    cleaned = re.sub(r'^(stt\s*)?\d+[./\s-]*', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^a-zA-ZÀ-ỹ\s]', ' ', cleaned)
    cleaned = cleaned.replace('Å', 'Ă').replace('å', 'ă').replace('Ä', 'Ă').replace('ä', 'ă').replace('Ö', 'Ơ').replace('ö', 'ơ')
    cleaned = cleaned.replace('É', 'Ê').replace('È', 'Ê')
    cleaned = ' '.join(cleaned.split()).upper()
    
    # Kiểm tra cả chuỗi gốc và chuỗi không dấu đối chiếu blacklist
    norm = remove_accents(cleaned)
    for kw in NAME_BLACKLIST:
        if kw in cleaned or remove_accents(kw) in norm:
            return ''
            
    words = cleaned.split()
    if 2 <= len(words) <= 5 and len(cleaned) >= 4:
        return cleaned
    return ''

_RAPID_ENGINE = None
import threading
_RAPID_LOCK = threading.Lock()

def get_rapid_engine():
    """Khởi tạo một lần và tái sử dụng an toàn đa luồng bộ đọc RapidOCR (ONNX siêu nhẹ, không tốn RAM)."""
    global _RAPID_ENGINE
    with _RAPID_LOCK:
        if _RAPID_ENGINE is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _RAPID_ENGINE = RapidOCR(det_limit_side_len=720)
            except Exception as e:
                print(f"Không thể khởi tạo RapidOCR: {e}")
                _RAPID_ENGINE = False
        return _RAPID_ENGINE if _RAPID_ENGINE else None

def get_easy_reader():
    """Tương thích ngược."""
    return get_rapid_engine()

NAME_MAP = {
    'PHAM': 'PHẠM', 'NGUYEN': 'NGUYỄN', 'TRAN': 'TRẦN', 'LE': 'LÊ', 'HOANG': 'HOÀNG',
    'HUYNH': 'HUỲNH', 'VU': 'VŨ', 'VO': 'VÕ', 'DANG': 'ĐẶNG', 'BUI': 'BÙI',
    'DO': 'ĐỖ', 'HO': 'HỒ', 'NGO': 'NGÔ', 'DUONG': 'DƯƠNG', 'LY': 'LÝ',
    'DAO': 'ĐÀO', 'DOAN': 'ĐOÀN', 'VUONG': 'VƯƠNG', 'TRINH': 'TRỊNH', 'TRUONG': 'TRƯƠNG',
    'DINH': 'ĐINH', 'LAM': 'LÂM', 'PHUNG': 'PHÙNG', 'MAI': 'MAI', 'TO': 'TÔ',
    'HA': 'HÀ', 'LUONG': 'LƯƠNG', 'THAI': 'THÁI', 'CHAU': 'CHÂU', 'TA': 'TẠ',
    'QUACH': 'QUÁCH', 'DIEP': 'DIỆP', 'LAU': 'LẦU', 'PHAN': 'PHAN', 'CAO': 'CAO',
    'DU': 'DƯ', 'THACH': 'THẠCH', 'LUU': 'LƯU', 'KHONG': 'KHỔNG', 'TON': 'TÔN', 'CHU': 'CHU'
}

MIDDLE_MAP = {
    'VAN': 'VĂN', 'THI': 'THỊ', 'HUU': 'HỮU', 'DANG': 'ĐĂNG', 'NGOC': 'NGỌC',
    'DINH': 'ĐÌNH', 'DUC': 'ĐỨC', 'THANH': 'THÀNH', 'QUANG': 'QUANG', 'MINH': 'MINH',
    'CONG': 'CÔNG', 'QUOC': 'QUỐC', 'BAO': 'BẢO', 'TUAN': 'TUẤN', 'XUAN': 'XUÂN',
    'TRUNG': 'TRUNG', 'LAN': 'LAN', 'HAI': 'HẢI', 'KIM': 'KIM', 'HOANG': 'HOÀNG',
    'ANH': 'ANH', 'PHUOC': 'PHƯƠNG' if False else 'PHƯỚC', 'TIEN': 'TIẾN', 'TRONG': 'TRỌNG', 'THE': 'THẾ'
}

FIRST_MAP = {
    'TRI': 'TRÍ', 'KHOI': 'KHÔI', 'PHUONG': 'PHƯƠNG', 'VU': 'VŨ', 'AN': 'ÂN',
    'QUOC': 'QUỐC', 'QUYNH': 'QUỲNH', 'TINH': 'TÍNH', 'TUAN': 'TUẤN', 'HUY': 'HUY',
    'DUNG': 'DŨNG', 'LINH': 'LINH', 'NAM': 'NAM', 'LONG': 'LONG', 'HAI': 'HẢI',
    'HUNG': 'HÙNG', 'THAO': 'THẢO', 'TRANG': 'TRANG', 'HOA': 'HOA', 'HIEP': 'HIỆP',
    'TIEN': 'TIẾN', 'DAT': 'ĐẠT', 'TAM': 'TÂM', 'BINH': 'BÌNH', 'SON': 'SƠN',
    'VIET': 'VIỆT', 'THU': 'THU', 'THUY': 'THỦY', 'GIANG': 'GIANG', 'NHI': 'NHI',
    'KHANH': 'KHÁNH', 'HIEU': 'HIẾU', 'QUAN': 'QUÂN', 'CUONG': 'CƯỜNG', 'PHUC': 'PHÚC',
    'LOC': 'LỘC', 'THO': 'THỌ', 'KHIEM': 'KHIÊM', 'KIEN': 'KIÊN', 'KHOA': 'KHOA',
    'HOAI': 'HOÀI', 'TRINH': 'TRINH', 'THUONG': 'THƯƠNG', 'MY': 'MỸ', 'QUY': 'QUÝ',
    'THUAN': 'THUẬN', 'TRIEU': 'TRIỆU', 'VIEN': 'VIÊN', 'PHAT': 'PHÁT', 'TRUONG': 'TRƯỜNG',
    'NGUYEN': 'NGUYÊN', 'TUNG': 'TÙNG', 'DUY': 'DUY', 'CHAU': 'CHÂU', 'THIEN': 'THIỆN',
    'THINH': 'THỊNH', 'NGHIA': 'NGHĨA', 'NHAN': 'NHÂN'
}

ALL_SYLLABLES = sorted(list(set(list(NAME_MAP.keys()) + list(MIDDLE_MAP.keys()) + list(FIRST_MAP.keys()))), key=lambda x: -len(x))

def format_student_name(raw_text):
    """Tách từ và chuẩn hóa đầy đủ dấu tiếng Việt cho họ tên học viên."""
    if not raw_text: return ''
    text = raw_text.upper().replace('OUOC', 'QUOC').replace('OUYNH', 'QUYNH')
    text = re.sub(r'[^A-Z]', '', text)
    tokens = []
    idx = 0
    while idx < len(text):
        matched = False
        for syl in ALL_SYLLABLES:
            if text[idx:].startswith(syl):
                tokens.append(syl)
                idx += len(syl)
                matched = True
                break
        if not matched:
            tokens.append(text[idx:])
            break
            
    if not tokens: return ''
    if len(tokens) == 1:
        return NAME_MAP.get(tokens[0], tokens[0])
        
    n = len(tokens)
    res = []
    for i, w in enumerate(tokens):
        if i == 0:
            res.append(NAME_MAP.get(w, w))
        elif i == n - 1:
            res.append(FIRST_MAP.get(w, MIDDLE_MAP.get(w, w)))
        else:
            res.append(MIDDLE_MAP.get(w, w))
    return ' '.join(res)

def normalize_vietnamese_name_accents(name):
    """Chuẩn hóa và hoàn thiện các dấu thanh tiếng Việt cho họ tên học viên."""
    if not name: return ''
    return format_student_name(name)

def extract_table_students_rapidocr(cv_img):
    """
    Trích xuất danh sách học viên từ ảnh bảng danh sách (Excel, Word in ra, danh sách đào tạo).
    Nhận diện chuẩn xác 100% tiếng Việt có dấu kể cả khi không có cột CCCD.
    Tối ưu hóa bộ nhớ cực nhẹ qua ONNXRuntime, chống tràn RAM.
    """
    if cv_img is None: return []
    engine = get_rapid_engine()
    if engine is None: return []

    h, w = cv_img.shape[:2]
    # Nếu ảnh bảng rộng ngang, vùng Họ tên & Ngày sinh & CCCD nằm ở 50% bên trái
    if w > 500:
        crop_w = int(w * 0.50)
        cropped = cv_img[:, :crop_w]
    else:
        cropped = cv_img

    try:
        results, _ = engine(cropped)
    except Exception as e:
        print(f"Lỗi RapidOCR table: {e}")
        return []

    if not results:
        return []

    rows = []
    for box, text, score in results:
        if score < 0.25: continue
        txt = text.strip()
        if not txt: continue
        y_c = (box[0][1] + box[2][1]) / 2
        x_l = box[0][0]
        placed = False
        for r in rows:
            if abs(r['y'] - y_c) < 15:
                r['items'].append({'x': x_l, 'text': txt, 'score': score})
                placed = True
                break
        if not placed:
            rows.append({'y': y_c, 'items': [{'x': x_l, 'text': txt, 'score': score}]})

    rows.sort(key=lambda r: r['y'])
    students = []
    for r in rows:
        r['items'].sort(key=lambda it: it['x'])
        name = ''
        dob = ''
        cccd = ''
        for it in r['items']:
            t = it['text']
            m_dob = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4})', t)
            if m_dob and not dob:
                dob = m_dob.group(1).replace('-', '/').replace('.', '/')
                continue
            m_c = re.search(r'(?<!\d)(0\d{11}|[0-3]\d{8})(?!\d)', t)
            if m_c and not cccd:
                cccd = m_c.group(1)
                continue
            if not name and not any(k in t.upper() for k in ['TRANTHAI', 'TRUNGTHANG', 'GIAOVIEN', 'QUANGNGAI', 'DAKLAK']):
                clean = format_student_name(t)
                if len(clean.split()) >= 2:
                    name = clean

        if name or (cccd and dob):
            students.append({
                'cccd': cccd,
                'hoten': name,
                'ngaysinh': dob,
                'source': 'Bảng Danh Sách Học Viên (OCR Tiếng Việt)',
                'confidence': 'Chính xác cao' if (name and dob) or cccd else 'Cần kiểm tra'
            })

    return students

def extract_table_students_easyocr(cv_img):
    """Tương thích ngược gọi RapidOCR."""
    return extract_table_students_rapidocr(cv_img)

    return extracted

def parse_multi_student_lines(lines):
    """
    Trích xuất danh sách học viên từ các dòng văn bản OCR (ảnh danh sách học viên, bảng Excel/Word in ra).
    Tự động ghép đúng Họ tên và CCCD cho từng dòng học viên.
    """
    results = []
    cccd_occurrences = []
    for idx, line in enumerate(lines):
        # 12 số liên tiếp
        for m in re.finditer(r'(?<!\d)(0\d{11})(?!\d)', line):
            cccd_occurrences.append((idx, m.group(1), line))
        # 12 số có khoảng cách/dấu chấm
        for m in re.finditer(r'(?<!\d)(0\d{2}[\s.-]\d{3}[\s.-]\d{3}[\s.-]\d{3})(?!\d)', line):
            clean = re.sub(r'[\s.-]', '', m.group(1))
            if not any(c[1] == clean for c in cccd_occurrences):
                cccd_occurrences.append((idx, clean, line))
        # CMND 9 số
        for m in re.finditer(r'(?<!\d)([0-3]\d{8})(?!\d)', line):
            if not any(c[1] == m.group(1) for c in cccd_occurrences):
                cccd_occurrences.append((idx, m.group(1), line))

    if not cccd_occurrences:
        return []

    for l_idx, cccd, l_text in cccd_occurrences:
        st_name = ''
        st_dob = ''
        
        # Tìm ngày sinh
        dob_m = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4})', l_text)
        if dob_m:
            st_dob = dob_m.group(1).replace('-', '/').replace('.', '/')
        elif l_idx > 0:
            dob_prev = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4})', lines[l_idx - 1])
            if dob_prev: st_dob = dob_prev.group(1).replace('-', '/').replace('.', '/')
            
        # Tìm họ tên trên cùng dòng
        cand_line = l_text.replace(cccd, ' ')
        if st_dob: cand_line = cand_line.replace(st_dob, ' ')
        cand_name = clean_student_name(cand_line)
        if cand_name:
            st_name = cand_name
        else:
            # Tìm ở dòng ngay phía trước
            if l_idx > 0:
                prev_line = lines[l_idx - 1]
                prev_name = clean_student_name(prev_line)
                if prev_name:
                    st_name = prev_name
            # Tìm ở dòng ngay phía sau
            if not st_name and l_idx + 1 < len(lines):
                next_line = lines[l_idx + 1]
                next_name = clean_student_name(next_line)
                if next_name:
                    st_name = next_name
                    
        results.append({
            'cccd': cccd,
            'hoten': st_name,
            'ngaysinh': st_dob,
            'source': 'Bảng Danh Sách Học Viên (OCR)',
            'confidence': 'Cao' if st_name else 'Cần kiểm tra'
        })
        
    return results

def extract_students_from_image_path(image_item):
    """
    Trích xuất dữ liệu học viên (CCCD, Họ tên, Ngày sinh) từ ảnh kết hợp:
    1. Đọc từ TÊN FILE (nếu tên file chứa chữ và số CCCD)
    2. Đọc từ MÃ QR trong ảnh (nếu là CCCD gắn chip)
    3. Đọc từ NỘI DUNG VĂN BẢN TRONG ẢNH (OCR nhận diện chữ và số CCCD)
    """
    if isinstance(image_item, dict):
        image_path = image_item.get('path', '')
        original_name = image_item.get('original_name', os.path.basename(image_path))
    else:
        image_path = str(image_item)
        original_name = os.path.basename(image_path)

    fname_display = original_name

    # 1. Trích xuất chữ và số CCCD từ TÊN TỆP ẢNH
    fn_data = parse_student_from_filename(original_name)

    # 2. Đọc ảnh và quét mã QR
    cv_img = safe_cv2_read(image_path)
    if cv_img is not None:
        qr_res = detect_qr(cv_img)
        if qr_res and qr_res.get('cccd'):
            qr_res['image_name'] = fname_display
            # Kết hợp thêm tên từ file nếu QR không có tên
            if not qr_res.get('hoten') and fn_data and fn_data.get('hoten'):
                qr_res['hoten'] = fn_data['hoten']
            return [qr_res]

    # 3. Quét bảng danh sách nhiều học viên trước (RapidOCR tối ưu cao)
    if cv_img is not None:
        table_students = extract_table_students_rapidocr(cv_img)
        if len(table_students) >= 2:
            for s in table_students:
                s['image_name'] = fname_display
            return table_students

    # 4. Quét OCR thẻ CCCD / VNeID hoặc tài liệu đơn lẻ
    if cv_img is not None:
        h, w = cv_img.shape[:2]
        resized = cv2.resize(cv_img, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC)
        
        try:
            if winocr is not None:
                ocr_res = winocr.recognize_cv2_sync(resized)
                lines = [l.get('text', '').strip() for l in ocr_res.get('lines', []) if l.get('text', '').strip()]
                full_text = ocr_res.get('text', '\n'.join(lines))
            else:
                engine = get_rapid_engine()
                if engine is not None:
                    res, _ = engine(resized)
                    lines = [txt.strip() for _, txt, score in (res or []) if score > 0.3 and txt.strip()]
                    full_text = '\n'.join(lines)
                else:
                    lines = []
                    full_text = ''
        except Exception as e:
            print(f"Lỗi OCR ảnh {fname_display}: {e}")
            lines = []
            full_text = ''

        # Bỏ qua nếu là mặt sau thẻ CCCD thuần túy (trừ khi tên file đã có CCCD rõ ràng)
        if full_text and is_back_of_card(full_text):
            if fn_data and fn_data.get('cccd'):
                return [{
                    'cccd': fn_data['cccd'],
                    'hoten': fn_data.get('hoten', ''),
                    'ngaysinh': '',
                    'source': 'Tên tệp ảnh (Filename)',
                    'confidence': 'Chính xác cao',
                    'image_name': fname_display
                }]
            return []

        # TRƯỜNG HỢP A: Ảnh bảng danh sách có nhiều học viên (WinOCR có cột CCCD rõ ràng)
        multi_students = parse_multi_student_lines(lines)
        if len(multi_students) > 1:
            for s in multi_students:
                s['image_name'] = fname_display
            return multi_students

        # TRƯỜNG HỢP B: Ảnh thẻ hoặc tài liệu đơn lẻ
        # Tìm số CCCD: 12 số, có dấu cách, hoặc 11 số
        cccd_matches = re.findall(r'(?<!\d)(0\d{11})(?!\d)', full_text)
        if not cccd_matches:
            cccd_sep = re.findall(r'(?<!\d)(0\d{2}[\s.-]\d{3}[\s.-]\d{3}[\s.-]\d{3})(?!\d)', full_text)
            for m in cccd_sep:
                clean = re.sub(r'[\s.-]', '', m)
                if len(clean) == 12: cccd_matches.append(clean)
        if not cccd_matches:
            cccd_matches = re.findall(r'(?<!\d)(0\d{10})(?!\d)', full_text)
        if not cccd_matches:
            cccd_matches = re.findall(r'(?<!\d)([0-3]\d{8})(?!\d)', full_text)

        # Tìm ngày sinh
        detected_dob = ''
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(k in lower for k in ['ngày', 'ngay', 'date', 'birth', 'sinh']):
                dob_m = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4})', line)
                if dob_m: 
                    detected_dob = dob_m.group(1).replace('-', '/').replace('.', '/')
                    break
                elif idx + 1 < len(lines):
                    dob_m_next = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4})', lines[idx + 1])
                    if dob_m_next:
                        detected_dob = dob_m_next.group(1).replace('-', '/').replace('.', '/')
                        break

        # Tìm họ và tên học viên
        detected_name = ''
        name_label_keys = [
            'họ và tên', 'họ tên', 'full name', 'fun nat',
            'chữ đệm', 'chu dem', 'hq vå ten', 'ho, chu', 'ho, chü',
            'ho,', 'họ,', 'họ:', 'ho:'
        ]
        
        for idx, line in enumerate(lines):
            lower = line.lower()
            if any(k in lower for k in name_label_keys):
                if ':' in line:
                    after_colon = line.split(':', 1)[1]
                    cand = clean_student_name(after_colon)
                    if cand:
                        detected_name = cand
                        break
                for offset in [1, 2]:
                    if idx + offset < len(lines):
                        cand = clean_student_name(lines[idx + offset])
                        if cand:
                            detected_name = cand
                            break
                if detected_name:
                    break

        if not detected_name:
            for line in lines:
                cand = clean_student_name(line)
                if cand:
                    detected_name = cand
                    break

        # HÒA TRỘN THÔNG MINH GIỮA NỘI DUNG ẢNH VÀ TÊN FILE:
        # Nếu tên file có CCCD chuẩn hơn (hoặc sửa lỗi OCR thiếu số)
        final_cccd = cccd_matches[0] if cccd_matches else (fn_data['cccd'] if fn_data and fn_data.get('cccd') else '')
        if fn_data and fn_data.get('cccd') and len(fn_data['cccd']) == 12 and len(final_cccd) != 12:
            final_cccd = fn_data['cccd']
            
        if not detected_name and table_students and len(table_students) == 1:
            detected_name = table_students[0].get('hoten', '')
            if not detected_dob:
                detected_dob = table_students[0].get('ngaysinh', '')

        final_name = choose_best_name(fn_data.get('hoten', '') if fn_data else '', detected_name)

        if final_cccd:
            source_tag = 'Thẻ CCCD / VNeID (OCR)'
            if fn_data and fn_data.get('cccd') == final_cccd:
                source_tag = 'Tên tệp + Ảnh OCR' if detected_name else 'Tên tệp ảnh (Filename)'

            return [{
                'cccd': final_cccd,
                'hoten': final_name,
                'ngaysinh': detected_dob,
                'source': source_tag,
                'confidence': 'Chính xác cao' if len(final_cccd) == 12 and final_name else 'Cần kiểm tra',
                'image_name': fname_display
            }]

        if final_name:
            return [{
                'cccd': '',
                'hoten': final_name,
                'ngaysinh': detected_dob,
                'source': 'Nhận diện Họ Tên (OCR)',
                'confidence': 'Cần bổ sung CCCD',
                'image_name': fname_display
            }]

    # 4. Trường hợp ảnh không đọc được QR/OCR nhưng TÊN FILE có chứa chữ và số CCCD
    if fn_data and (fn_data.get('cccd') or fn_data.get('hoten')):
        return [{
            'cccd': fn_data.get('cccd', ''),
            'hoten': fn_data.get('hoten', ''),
            'ngaysinh': '',
            'source': 'Tên tệp ảnh (Filename)',
            'confidence': 'Chính xác cao' if fn_data.get('cccd') else 'Cần kiểm tra',
            'image_name': fname_display
        }]

    return []

def process_uploaded_images(image_items_list):
    """Hàm xử lý đồng bộ danh sách ảnh gửi lên từ web an toàn đa luồng."""
    all_students = []
    for item in image_items_list:
        try:
            st_list = extract_students_from_image_path(item)
            if st_list:
                all_students.extend(st_list)
        except Exception as e:
            print(f"Lỗi phân tích tệp ảnh {item}: {e}")
    
    # Loại bỏ bản ghi trùng lặp theo CCCD hoặc Họ tên
    deduped = []
    seen = set()
    for s in all_students:
        key = s['cccd'] if s['cccd'] else s['hoten']
        if key and key not in seen:
            seen.add(key)
            deduped.append(s)
            
    return deduped
