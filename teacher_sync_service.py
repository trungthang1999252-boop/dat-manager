import os
import re
import csv
import io
import time
import urllib.request
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

INDEX_SHEET_ID = '1FRC_xHYy36NntmKyRhXlXL0csqybXqE64FQUA7NcTVI'
TEACHER_SHEET_ID = '1ow-dU4w7UmoCpzP6w0lDBhk5kAorIK8x72jdNcO19_Y'

_courses_cache = {'time': 0, 'data': [], 'all': [], 'active': []}
_teachers_cache = {'time': 0, 'data': []}

def is_course_allowed(course_name):
    """
    Quy tắc lọc khoá học cho tính năng 'Đẩy danh sách theo giáo viên':
    1. Bỏ qua khoá Test (CD_TestMayDat...)
    2. Xoá toàn bộ hạng CE (CD_01CE đến CD_29CE...)
    3. Xoá toàn bộ nâng hạng C / CM (B LÊN C, B LÊN CM, CM...)
    4. Xoá toàn bộ hạng C (CD_128C đến CD_144C...) - chú ý không xoá C1
    5. Xoá những khoá cũ của hạng C1 từ 13 trở về trước (C1 <= 13). Chỉ giữ C1 > 13 (CD_14C1, CD_15C1...)
    6. Xoá những khoá cũ của hạng B.01 từ 108 trở về trước (B.01 <= 108). Chỉ giữ B.01 > 108 (CD_109B.01, CD_110B.01...)
    7. Xoá những khoá cũ của hạng B từ 236 trở về trước (B <= 236). Chỉ giữ B > 236 (CD_237B, CD_238B...)
    """
    if not course_name:
        return True, "Không có tên khoá"

    c = str(course_name).strip()
    c_up = c.upper()

    # 1. Bỏ qua khoá Test
    if 'TEST' in c_up:
        return False, "Khoá Test"

    # 2. Xoá toàn bộ hạng CE
    if 'CE' in c_up:
        return False, "Hạng CE"

    # 3. Xoá toàn bộ nâng hạng C / CM
    if any(k in c_up for k in ['LÊN C', 'LÊN CM', 'B2 LÊN', 'B LÊN']) or re.search(r'\bCM\b', c_up):
        return False, "Nâng hạng C / CM"

    # 4. Xoá toàn bộ hạng C (CD_128C đến CD_144C...) - chú ý không xoá C1
    m_c = re.search(r'(\d+)C(?!\d|[E1])', c_up)
    if m_c:
        return False, f"Hạng C ({m_c.group(1)}C)"

    # 5. Xoá những khoá cũ của hạng C1 từ 13 trở về trước (C1 <= 13)
    m_c1 = re.search(r'(\d+)\s*C1', c_up)
    if m_c1:
        c1_num = int(m_c1.group(1))
        if c1_num <= 13:
            return False, f"Hạng C1 cũ <= 13 ({c1_num}C1)"
        else:
            return True, f"Hạng C1 mới > 13 ({c1_num}C1)"

    # 6. Kiểm tra khoá B.01 / B1 / TĐ (CD_102B.01 đến CD_112B.01)
    # B.01 từ 108 trở về trước tương ứng với B <= 236
    m_b01 = re.search(r'(\d+)\s*B\.?01', c_up) or re.search(r'(\d+)\s*(?:TĐ|TD|B1)\b', c_up)
    if m_b01:
        b01_num = int(m_b01.group(1))
        if b01_num <= 108:
            return False, f"Hạng B.01 cũ <= 108 ({b01_num}B.01)"
        else:
            return True, f"Hạng B.01 mới > 108 ({b01_num}B.01)"

    # 7. Xoá những khoá cũ của hạng B từ 236 trở về trước (B <= 236)
    m_b = re.search(r'(\d+)\s*B(?:2)?(?!\.01|\d)', c_up) or re.search(r'K(\d+)\s*B', c_up)
    if m_b:
        b_num = int(m_b.group(1))
        if b_num <= 236:
            return False, f"Hạng B cũ <= 236 ({b_num}B)"
        else:
            return True, f"Hạng B mới > 236 ({b_num}B)"

    # Mặc định: giữ lại khoá nếu không vi phạm bất kỳ quy tắc cũ nào ở trên
    return True, "Khoá hợp lệ"

def strip_accents(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFD', str(s))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.replace('đ', 'd').replace('Đ', 'D').lower().strip()

def normalize_name(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFC', str(s))
    return re.sub(r'\s+', ' ', s).strip()

def fetch_csv(url, timeout=10):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)',
        'Accept': 'text/csv,*/*'
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode('utf-8-sig', errors='replace')
    return list(csv.reader(io.StringIO(content)))

def get_teachers_list(force=False):
    global _teachers_cache
    now = time.time()
    if not force and _teachers_cache['data'] and (now - _teachers_cache['time'] < 1800):
        return _teachers_cache['data']

    url = f'https://docs.google.com/spreadsheets/d/{TEACHER_SHEET_ID}/gviz/tq?tqx=out:csv'
    try:
        rows = fetch_csv(url, timeout=10)
    except Exception:
        url_alt = f'https://docs.google.com/spreadsheets/d/{TEACHER_SHEET_ID}/export?format=csv'
        rows = fetch_csv(url_alt, timeout=12)

    teachers = []
    seen = set()
    for r in rows[1:]:
        if len(r) > 3 and r[3].strip():
            raw_name = normalize_name(r[3])
            clean = strip_accents(raw_name)
            if not clean or clean in seen or any(k in clean for k in ['ho va ten', 'giao vien', 'stt']):
                continue
            seen.add(clean)
            short = normalize_name(r[5]) if len(r) > 5 else ''
            car = re.sub(r'[^A-Za-z0-9]', '', r[1].strip().upper()) if len(r) > 1 else ''
            code = r[23].strip() if len(r) > 23 else ''
            teachers.append({
                'name': raw_name,
                'clean': clean,
                'short': short,
                'short_clean': strip_accents(short),
                'car': car,
                'code': code
            })

    _teachers_cache = {'time': now, 'data': teachers}
    return teachers

def get_courses_catalog(force=False, filter_active=True):
    """
    Lấy danh mục các khoá học trên Google Sheet.
    Mặc định filter_active=True: tự động loại bỏ các khoá cũ theo yêu cầu:
    - B <= 236 và B.01 <= 108
    - C1 <= 13
    - Toàn bộ C và CE
    - Khoá Test
    """
    global _courses_cache
    now = time.time()
    cache_key = 'active' if filter_active else 'all'
    if not force and _courses_cache.get(cache_key) and (now - _courses_cache['time'] < 1800):
        return _courses_cache[cache_key]

    url = f'https://docs.google.com/spreadsheets/d/{INDEX_SHEET_ID}/export?format=csv'
    try:
        rows = fetch_csv(url, timeout=10)
    except Exception:
        url_alt = f'https://docs.google.com/spreadsheets/d/{INDEX_SHEET_ID}/gviz/tq?tqx=out:csv'
        rows = fetch_csv(url_alt, timeout=12)

    all_courses = []
    active_courses = []
    seen = set()
    for r in rows:
        for ci, cell in enumerate(r):
            m = re.search(r'/d/([a-zA-Z0-9-_]{20,})', cell)
            if m:
                sid = m.group(1)
                if sid not in seen and sid not in (INDEX_SHEET_ID, TEACHER_SHEET_ID):
                    seen.add(sid)
                    cname = r[ci-1].strip() if ci > 0 and r[ci-1].strip() else f'Khoa_{len(seen)}'
                    c_norm = normalize_name(cname)
                    item = {
                        'course': c_norm,
                        'sheet_id': sid
                    }
                    all_courses.append(item)
                    if is_course_allowed(c_norm)[0]:
                        active_courses.append(item)

    _courses_cache['time'] = now
    _courses_cache['all'] = all_courses
    _courses_cache['active'] = active_courses
    _courses_cache['data'] = active_courses if filter_active else all_courses

    return active_courses if filter_active else all_courses

def detect_hang_from_course(course_str):
    if not course_str:
        return 'B'
    c_up = course_str.upper()
    if 'CE' in c_up:
        return 'Cm-CE'
    if 'C1' in c_up:
        return 'C1'
    if 'C' in c_up and 'B' not in c_up:
        return 'C'
    if 'B.01' in c_up or 'B1' in c_up or 'B01' in c_up or 'TĐ' in c_up or 'TD' in c_up:
        return 'B.01'
    if re.search(r'\d+B\b', c_up) or c_up.endswith('B'):
        return 'B'
    if 'B2' in c_up:
        return 'B2'
    return 'B'

def match_teacher(query_teacher_name, teacher_obj, cell_value):
    """
    Khớp tên giáo viên theo đúng yêu cầu:
    Kiểm tra xem ô đó có chứa đúng họ tên đầy đủ của giáo viên mà người dùng nhập hay không
    (bao gồm tiền tố Thầy/Cô/GV hoặc giáo viên dạy cặp ghép '-').
    Tuyệt đối không so sánh lỏng lẻo theo 1 từ tên gọi để tránh nhầm với giáo viên khác.
    """
    if not cell_value or not query_teacher_name:
        return False

    val_clean = strip_accents(cell_value)
    q_clean = strip_accents(query_teacher_name)

    if not val_clean or not q_clean or len(q_clean) < 3:
        return False

    # Khớp trực tiếp họ tên đầy đủ
    if q_clean in val_clean:
        return True

    # Khớp nếu dạy cặp (ví dụ: 'Thắng - Sang', 'Thắng-Huy', 'Trần Thái Trung Thắng - ...')
    parts = re.split(r'[-/+,&]', val_clean)
    for p in parts:
        p_clean = re.sub(r'^(thay|co|gv)\s+', '', p).strip()
        if p_clean == q_clean or q_clean in p_clean:
            return True

    return False

def parse_course_students(course_info, query_teacher_name, teacher_obj=None):
    cname = course_info['course']
    sid = course_info['sheet_id']
    gviz_url = f'https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv'
    
    try:
        rows = fetch_csv(gviz_url, timeout=8)
    except Exception:
        try:
            url_alt = f'https://docs.google.com/spreadsheets/d/{sid}/export?format=csv'
            rows = fetch_csv(url_alt, timeout=8)
        except Exception:
            return []

    if not rows or len(rows) < 2:
        return []

    # Find header row in first 10 rows
    h_idx_name = 1
    h_idx_gv = None
    h_idx_cccd = None
    h_idx_mahv = None
    h_idx_dob = None
    h_idx_xe = None
    h_idx_addr = None

    for r_idx, row in enumerate(rows[:10]):
        row_clean = [strip_accents(c) for c in row]
        for c_idx, val in enumerate(row_clean):
            if any(k in val for k in ['ho va ten', 'ho ten', 'ten hoc vien']) and h_idx_name == 1:
                h_idx_name = c_idx
            elif any(k in val for k in ['ten gv', 'giao vien', 'gv']) and h_idx_gv is None:
                h_idx_gv = c_idx
            elif any(k in val for k in ['cccd', 'cmnd', 'can cuoc']) and h_idx_cccd is None:
                h_idx_cccd = c_idx
            elif any(k in val for k in ['mahv', 'mshv', 'ma hoc vien', 'ma so']) and h_idx_mahv is None:
                h_idx_mahv = c_idx
            elif any(k in val for k in ['birthday', 'ngay sinh', 'ngaysinh', 'nam sinh']) and h_idx_dob is None:
                h_idx_dob = c_idx
            elif any(k in val for k in ['phan xe', 'xe', 'bien so']) and h_idx_xe is None:
                h_idx_xe = c_idx
            elif any(k in val for k in ['noi cu tru', 'dia chi', 'cu tru']) and h_idx_addr is None:
                h_idx_addr = c_idx

        if h_idx_gv is not None:
            break

    hang = detect_hang_from_course(cname)
    q_clean = strip_accents(query_teacher_name)
    matched_students = []

    for row in rows:
        if not row or len(row) < 2:
            continue

        row_vals = [normalize_name(c) for c in row]

        # Kiểm tra xem trên CÙNG HÀNG này có ô nào chứa tên giáo viên được nhập hay không
        matched = False
        matched_gv_val = ''

        for c_i, val in enumerate(row_vals):
            if c_i == h_idx_name:
                continue  # Tuyệt đối không kiểm tra cột họ tên học viên

            if match_teacher(query_teacher_name, teacher_obj, val):
                matched = True
                matched_gv_val = val
                break

        if not matched:
            continue

        # Lấy tên học viên tương ứng trên CÙNG HÀNG đó
        hoten = row_vals[h_idx_name] if h_idx_name < len(row_vals) else (row_vals[1] if len(row_vals) > 1 else '')
        hoten = normalize_name(hoten).upper()

        # Validate tên học viên
        if not hoten or len(hoten) < 3 or re.match(r'^\d+$', hoten):
            continue
        if any(k in strip_accents(hoten) for k in ['ho va ten', 'danh sach', 'stt', 'nguong', 'he thap phan', 'ngay chay']):
            continue

        # Không lấy dòng nếu họ tên học viên trùng khớp hoàn toàn với tên giáo viên tìm kiếm (tránh tự khớp nhầm)
        if strip_accents(hoten) == q_clean:
            # Chỉ chấp nhận nếu có cột giáo viên riêng biệt chứa tên giáo viên
            has_other_teacher_cell = any(c_i != h_idx_name and match_teacher(query_teacher_name, teacher_obj, row_vals[c_i]) for c_i in range(len(row_vals)))
            if not has_other_teacher_cell:
                continue

        # Extract CCCD trên cùng hàng
        cccd = ''
        if h_idx_cccd is not None and h_idx_cccd < len(row_vals):
            cccd = re.sub(r'\D', '', row_vals[h_idx_cccd])
        if not cccd or len(cccd) not in (9, 12):
            for v in row_vals:
                digits = re.sub(r'\D', '', v)
                if len(digits) in (9, 12):
                    cccd = digits
                    break

        # Extract MSHV trên cùng hàng
        mahv = ''
        if h_idx_mahv is not None and h_idx_mahv < len(row_vals):
            mahv = row_vals[h_idx_mahv].strip()
        if not mahv or '52001-' not in mahv:
            for v in row_vals:
                if '52001-' in v:
                    m = re.search(r'52001-[\d-]+', v)
                    if m:
                        mahv = m.group(0)
                        break

        # Extract Birthday trên cùng hàng
        ngaysinh = ''
        if h_idx_dob is not None and h_idx_dob < len(row_vals):
            ngaysinh = row_vals[h_idx_dob].strip()
        if not ngaysinh or not re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', ngaysinh):
            for v in row_vals:
                m = re.search(r'\b\d{1,2}/\d{1,2}/\d{4}\b', v)
                if m:
                    ngaysinh = m.group(0)
                    break

        # Extract Car trên cùng hàng
        xe = ''
        if h_idx_xe is not None and h_idx_xe < len(row_vals):
            xe = row_vals[h_idx_xe].strip().upper()
        if not xe or not re.search(r'[1-9]\d[A-Z]', xe):
            for v in row_vals:
                m = re.search(r'\b[1-9]\d[A-Z][-\s.]?\d{4,5}\b', v.upper())
                if m:
                    xe = m.group(0).replace(' ', '').replace('-', '').replace('.', '')
                    break

        # Extract Address trên cùng hàng
        noicutru = ''
        if h_idx_addr is not None and h_idx_addr < len(row_vals):
            noicutru = row_vals[h_idx_addr].strip()
        if not noicutru:
            for v in row_vals:
                if any(k in strip_accents(v) for k in ['xa ', 'phuong ', 'thi tran ', 'tinh ', 'tp ', 'huyen ']):
                    noicutru = v.strip()
                    break

        matched_students.append({
            'hoten': hoten,
            'cccd': cccd,
            'mahv': mahv,
            'ngaysinh': ngaysinh,
            'khoa': cname,
            'hang': hang,
            'xe': xe,
            'noicutru': noicutru,
            'giaovien': matched_gv_val or query_teacher_name,
            'ghichu': f'GV: {matched_gv_val or query_teacher_name}'
        })

    return matched_students

def scan_teacher_students(query_teacher_name, selected_courses=None, max_workers=25):
    """
    Quét danh sách học viên theo giáo viên trên Google Sheet.
    Tự động áp dụng quy tắc lọc bỏ khoá cũ (B <= 236, C1 <= 13, toàn bộ C và CE).
    """
    t0 = time.time()
    teachers = get_teachers_list()
    q_clean = strip_accents(query_teacher_name)
    
    t_obj = next((t for t in teachers if t['clean'] == q_clean or q_clean in t['clean']), None)
    teacher_display_name = t_obj['name'] if t_obj else query_teacher_name

    # Lấy danh mục các khoá học mới đang học
    courses = get_courses_catalog(filter_active=True)
    if selected_courses and selected_courses != 'all':
        if isinstance(selected_courses, str):
            selected_courses = [selected_courses]
        target_courses = [c for c in courses if is_course_allowed(c['course'])[0] and (c['course'] in selected_courses or any(sc in c['course'] for sc in selected_courses))]
        if not target_courses:
            target_courses = courses
    else:
        target_courses = courses

    all_students = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(parse_course_students, c, teacher_display_name, t_obj): c for c in target_courses}
        for f in as_completed(futures):
            try:
                res = f.result()
                if res:
                    all_students.extend(res)
            except Exception:
                pass

    # Deduplicate
    seen = set()
    unique_students = []
    by_course = defaultdict(list)

    for s in all_students:
        key = s['mahv'] or s['cccd'] or (s['hoten'] + '_' + s['ngaysinh'])
        if key and key not in seen:
            seen.add(key)
            unique_students.append(s)
            by_course[s['khoa']].append(s)

    elapsed = time.time() - t0
    return {
        'success': True,
        'teacher_name': teacher_display_name,
        'teacher_obj': t_obj,
        'total_scanned_courses': len(target_courses),
        'total_students_found': len(unique_students),
        'courses_with_students': len(by_course),
        'by_course': dict(by_course),
        'students': unique_students,
        'scan_time_seconds': round(elapsed, 2)
    }

def purge_obsolete_courses(db):
    """
    Xoá các khoá học cũ không hợp lệ khỏi CSDL và Roster của App:
    - Hạng B <= 236 và B.01 <= 108
    - Hạng C1 <= 13
    - Toàn bộ hạng C và CE
    - Khoá Test
    """
    obsolete_courses = set()
    with db._lock:
        for s in list(db.students.values()):
            khoa = s.get('khoa')
            if khoa and not is_course_allowed(khoa)[0]:
                obsolete_courses.add(khoa)
        for r in list(db.roster.values()):
            khoa = r.get('khoa')
            if khoa and not is_course_allowed(khoa)[0]:
                obsolete_courses.add(khoa)

    if obsolete_courses:
        db.delete_courses(list(obsolete_courses))
        return sorted(list(obsolete_courses))
    return []

def import_students_to_database(students, db):
    """
    Nạp toàn bộ học viên vừa tìm được vào CSDL và Roster của App.
    Đảm bảo học viên xuất hiện ngay trên giao diện theo từng khoá.
    Tự động dọn dẹp các khoá cũ không hợp lệ theo yêu cầu của nút 'Đẩy DS theo giáo viên'.
    """
    from datetime import datetime
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    # 1. Tự động dọn dẹp triệt để các khoá cũ khỏi CSDL & Roster
    purged_courses = purge_obsolete_courses(db)

    # 2. Lọc danh sách nạp: chỉ cho phép các khoá học hợp lệ
    valid_students = []
    for s in students:
        khoa = s.get('khoa', '').strip()
        if not khoa or is_course_allowed(khoa)[0]:
            valid_students.append(s)

    imported_count = 0
    courses_affected = set()

    for s in valid_students:
        hoten = s.get('hoten', '').strip()
        cccd = s.get('cccd', '').strip()
        mahv = s.get('mahv', '').strip()
        khoa = s.get('khoa', '').strip()
        hang = s.get('hang', '').strip()
        ngaysinh = s.get('ngaysinh', '').strip()
        ghichu = s.get('ghichu', '').strip()

        if not hoten and not cccd and not mahv:
            continue

        db.unmark_student_deleted(mahv=mahv, cccd=cccd, hoten=hoten)
        s['_force_unmark_deleted'] = True

        # 1. Thêm vào Roster
        db.add_to_roster({
            'hoten': hoten,
            'cccd': cccd,
            'mahv': mahv,
            'ngaysinh': ngaysinh,
            'khoa': khoa,
            'hang': hang,
            'ghichu': ghichu,
            'sdt': s.get('sdt', '')
        }, auto_save=False)

        # 2. Thêm hoặc cập nhật trong self.students (CSDL hiển thị chính trên App)
        target_key = mahv or cccd or hoten
        existing = None
        if target_key in db.students:
            existing = db.students[target_key]
        else:
            for k, cur_s in db.students.items():
                if (cccd and cur_s.get('cccd') == cccd) or (mahv and cur_s.get('mahv') == mahv):
                    existing = cur_s
                    break
                elif hoten and strip_accents(cur_s.get('hoten')) == strip_accents(hoten):
                    existing = cur_s
                    break

        if existing:
            # Cập nhật thông tin nếu thiếu
            if not existing.get('khoa') and khoa: existing['khoa'] = khoa
            if not existing.get('hang') and hang: existing['hang'] = hang
            if not existing.get('ngaysinh') and ngaysinh: existing['ngaysinh'] = ngaysinh
            if not existing.get('mahv') and mahv: existing['mahv'] = mahv
            if not existing.get('cccd') and cccd: existing['cccd'] = cccd
            existing['is_my_query'] = True
            db.students[target_key] = db.calculate_progress(existing)
        else:
            new_st = {
                'hoten': hoten,
                'cccd': cccd,
                'mahv': mahv,
                'ngaysinh': ngaysinh,
                'khoa': khoa,
                'hang': hang,
                'sdt': '',
                'quangduong': 0.0,
                'thoigian': 0.0,
                'thoigian_dem': 0.0,
                'thoigian_tudong': 0.0,
                'songay_str': '0/5 ngày',
                'songay_dat': False,
                'quangduong_dat': False,
                'thoigian_dat': False,
                'thoigian_dem_dat': False,
                'thoigian_tudong_dat': False,
                'cabin_uploaded': False,
                'cabin_status': '',
                'cabin_image_url': '',
                'so_phien_hople': 0,
                'so_phien_loi': 0,
                'phien_status': 'Chưa tra phiên',
                'phien_loi_detail': [],
                'phien_recoverable_cars': [],
                'phien_permanent_reasons': [],
                'has_lythuyet': False,
                'lythuyet_pdf_url': None,
                'pdf_url': None,
                'ketqua': 'Chưa tra DAT',
                'is_my_query': True,
                'created_at': now_str,
                'updated_at': now_str,
                'note': ghichu
            }
            db.students[target_key] = db.calculate_progress(new_st)

        imported_count += 1
        if khoa:
            courses_affected.add(khoa)

    # Lưu lại CSDL & Roster
    db._save_roster()
    db._save()
    db.recalculate_all()
    
    import database
    database.notify_data_changed()

    return {
        'imported_count': imported_count,
        'courses_affected': sorted(list(courses_affected)),
        'purged_courses': purged_courses
    }
