import fitz  # PyMuPDF
import re
import requests

PERMANENT_ERROR_CODES = {
    'B-0015': 'Phiên học vi phạm thời gian (nhỏ hơn 5 phút/phiên)',
    'B-0017': 'Phiên học vi phạm thời gian (lớn hơn 10 giờ/phiên)',
    'B-0020': 'Vi phạm thời gian: 2 phiên liên tiếp cách nhau dưới 15 phút',
    '*': 'Dữ liệu bị SGTVT từ chối (Sở từ chối)',
    '4': 'Biển số xe sai định dạng',
    'Y/C CHẠY BÙ': 'Vận tốc TB < 20 km/h hoặc thời gian quá ngắn (Yêu cầu chạy bù)'
}

def analyze_phien_pdf(pdf_path_or_bytes):
    """
    Phân tích file PDF báo cáo phiên học (_phienhoc.pdf):
    - Trích xuất thông tin từng phiên bị lỗi (Bảng III và chi tiết các phiên).
    - Phân loại lỗi vĩnh viễn (không thể đồng bộ, bắt buộc chạy bù) vs lỗi có thể cứu được (/dongbo).
    - Trích xuất biển số xe bị lỗi để chuẩn bị tham số chạy lệnh /dongbo.
    """
    if isinstance(pdf_path_or_bytes, (bytes, bytearray)):
        doc = fitz.open(stream=pdf_path_or_bytes, filetype="pdf")
    elif str(pdf_path_or_bytes).startswith(('http://', 'https://')):
        resp = requests.get(pdf_path_or_bytes, timeout=15)
        if resp.status_code != 200:
            return {'success': False, 'error': f'Không thể tải PDF ({resp.status_code})'}
        doc = fitz.open(stream=resp.content, filetype="pdf")
    else:
        doc = fitz.open(pdf_path_or_bytes)

    result = {
        'success': True,
        'mahv': '',
        'hoten': '',
        'khoa': '',
        'total_phiens': 0,
        'so_phien_hople': 0,
        'so_phien_loi': 0,
        'error_sessions': [],
        'has_permanent_error': False,
        'has_recoverable_error': False,
        'recoverable_cars': [],
        'permanent_reasons': []
    }

    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"

    # Trích xuất mã học viên, họ tên từ trang 1
    m_mahv = re.search(r'Mã học viên\s*[:\n]\s*([0-9\-\_]+)', full_text)
    if m_mahv:
        result['mahv'] = m_mahv.group(1).strip()

    m_name = re.search(r'Tên học viên\s*[:\n]\s*([^\n\r]+)', full_text)
    if m_name:
        result['hoten'] = m_name.group(1).strip()

    m_khoa = re.search(r'Tên khoá học\s*[:\n]\s*([^\n\r]+)', full_text)
    if m_khoa:
        result['khoa'] = m_khoa.group(1).strip()

    # Trích xuất tổng phiên, hợp lệ, lỗi từ trang đầu
    m_tl = re.search(r'Số phiên lỗi\s*[:\n]\s*(\d+)', full_text)
    if m_tl:
        result['so_phien_loi'] = int(m_tl.group(1))

    m_hl = re.search(r'Số phiên hợp lệ\s*[:\n]\s*(\d+)', full_text)
    if m_hl:
        result['so_phien_hople'] = int(m_hl.group(1))

    # Tìm bảng III ở trang 2 hoặc các trang tiếp theo
    error_list = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text_page = page.get_text()
        if 'THỐNG KÊ CÁC PHIÊN BỊ LỖI' in text_page:
            tabs = page.find_tables()
            for t in tabs:
                df = t.extract()
                for row in df:
                    row_str = " ".join([str(c) for c in row if c])
                    # Bỏ qua dòng tiêu đề
                    if 'Mãlỗi' in row_str or 'Phiênđàotạo' in row_str or 'Trạngthái' in row_str:
                        continue
                    
                    # Tìm mã lỗi
                    ma_loi = ''
                    for code in ['B-0015', 'B-0017', 'B-0020', 'B-0010', 'B-0011', 'C-0003', 'C-0010', '503', '409', '103', '-99', 'null']:
                        if code in row_str:
                            ma_loi = code
                            break
                    if not ma_loi and ('Nhận diện' in row_str or 'nhận diện' in row_str):
                        ma_loi = '103'

                    # Tìm biển số xe
                    m_bs = re.search(r'(\d{2}[A-Z]\d{4,6})', row_str)
                    bienso = m_bs.group(1) if m_bs else ''

                    # Tìm tỉ lệ nhận diện
                    m_nd = re.search(r'Nhận diện\s*([\d\.]+)%', row_str, re.IGNORECASE)
                    nd_pct = float(m_nd.group(1)) if m_nd else None

                    # Tìm thời gian phiên
                    m_time = re.search(r'(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})', row_str)
                    phien_time = m_time.group(1) if m_time else ''

                    if ma_loi or bienso or nd_pct:
                        is_perm = (ma_loi in PERMANENT_ERROR_CODES) or ('chạy bù' in row_str.lower())
                        error_list.append({
                            'ma_loi': ma_loi or 'Chưa lên TC',
                            'biensoxe': bienso,
                            'nhan_dien_pct': nd_pct,
                            'phien_time': phien_time,
                            'raw_text': row_str[:120],
                            'is_permanent': is_perm,
                            'ly_do': PERMANENT_ERROR_CODES.get(ma_loi, 'Phiên chưa đồng bộ lên Tổng cục (xử lý qua /dongbo)')
                        })

    # Ngoài ra quét các trang chi tiết từng phiên để phát hiện mã phản hồi TC
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        ptxt = page.get_text()
        if 'Mã phản hồi TC:' in ptxt or 'Chưa lên TC' in ptxt or 'Không lên TC' in ptxt:
            # Tách theo từng Phiên #X
            phien_blocks = re.split(r'(Phiên\s*#\d+)', ptxt)
            for i in range(1, len(phien_blocks), 2):
                header = phien_blocks[i]
                body = phien_blocks[i+1] if i+1 < len(phien_blocks) else ''
                if 'Chưa lên TC' in body or 'Không lên TC' in body or 'Y/c chạy bù' in body:
                    m_code = re.search(r'Mã phản hồi TC:\s*([A-Za-z0-9_-]+)', body)
                    code = m_code.group(1) if m_code else ''
                    m_bs = re.search(r'Biển số\s*\n\s*(\d{2}[A-Z]\d{4,6})', body)
                    bs = m_bs.group(1) if m_bs else ''
                    m_dur = re.search(r'Thời gian\s*\n\s*([^\n]+)', body)
                    dur = m_dur.group(1).strip() if m_dur else ''
                    m_km = re.search(r'Quãng đường\s*\n\s*([\d\.]+)\s*km', body)
                    km = float(m_km.group(1)) if m_km else 0.0
                    m_nd = re.search(r'Tỉ lệ nhận diện\s*\n\s*([\d\.]+)%', body)
                    nd = float(m_nd.group(1)) if m_nd else None

                    is_perm = False
                    lydo = 'Chưa lên TC'
                    if code in PERMANENT_ERROR_CODES:
                        is_perm = True
                        lydo = PERMANENT_ERROR_CODES[code]
                    elif 'Y/c chạy bù' in body:
                        is_perm = True
                        lydo = 'Vận tốc TB < 20 km/h hoặc thời gian quá ngắn (Yêu cầu chạy bù)'

                    # Thêm nếu chưa có trong error_list
                    exists = any(e.get('biensoxe') == bs and e.get('ma_loi') == code for e in error_list if bs and code)
                    if not exists and (bs or code):
                        error_list.append({
                            'ma_loi': code or ('103' if (nd is not None and nd < 90) else 'Chưa lên TC'),
                            'biensoxe': bs,
                            'duration': dur,
                            'km': km,
                            'nhan_dien_pct': nd,
                            'is_permanent': is_perm,
                            'ly_do': lydo
                        })

    result['error_sessions'] = error_list
    if error_list:
        result['so_phien_loi'] = max(result['so_phien_loi'], len(error_list))
        result['has_permanent_error'] = any(e['is_permanent'] for e in error_list)
        result['has_recoverable_error'] = any(not e['is_permanent'] for e in error_list)
        result['recoverable_cars'] = list(set([e['biensoxe'] for e in error_list if not e['is_permanent'] and e['biensoxe']]))
        result['permanent_reasons'] = list(set([e['ly_do'] for e in error_list if e['is_permanent']]))

    return result
