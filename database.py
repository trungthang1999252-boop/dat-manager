import os
import re
import json
import shutil
import unicodedata
import threading
import time
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
STUDENTS_FILE = os.path.join(DATA_DIR, 'students.json')
ROSTER_FILE = os.path.join(DATA_DIR, 'roster.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

_on_change_callbacks = []

def register_on_change_callback(cb):
    if cb not in _on_change_callbacks:
        _on_change_callbacks.append(cb)

def notify_data_changed():
    for cb in list(_on_change_callbacks):
        try:
            cb()
        except Exception as e:
            pass

def strip_vietnamese_accents(s):
    """Xóa bỏ toàn bộ dấu tiếng Việt và chuẩn hóa chữ thường để tìm kiếm thông minh."""
    if not s: return ''
    s = str(s).replace('Đ', 'D').replace('đ', 'd')
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

# Định mức chuẩn theo cấu hình yêu cầu của giáo viên & quy chuẩn Đào tạo lái xe
# Hạng B.01: Tổng giờ 12h, Bỏ qua giờ xe tự động
# Hạng C1: Tổng giờ 24h
# Hạng B: Tổng giờ 20h
BENCHMARKS = {
    'B.01':  {'km': 710.0, 'gio': 12.0, 'dem': 2.5, 'tudong_min': 0.0, 'days': 5},
    'B1':    {'km': 710.0, 'gio': 12.0, 'dem': 2.5, 'tudong_min': 0.0, 'days': 5},
    'B':     {'km': 810.0, 'gio': 20.0, 'dem': 3.0, 'tudong_min': 2.0, 'days': 5},
    'B2':    {'km': 810.0, 'gio': 20.0, 'dem': 3.0, 'tudong_min': 2.0, 'days': 5},
    'C1':    {'km': 825.0, 'gio': 24.0, 'dem': 2.0, 'tudong_min': 2.0, 'days': 5},
    'C':     {'km': 825.0, 'gio': 24.0, 'dem': 2.0, 'tudong_min': 2.0, 'days': 5},
    'B-CM':  {'km': 210.0, 'gio': 5.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 0},
    'B-Cm':  {'km': 210.0, 'gio': 5.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 0},
    'CM-CE': {'km': 210.0, 'gio': 5.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 2},
    'Cm-CE': {'km': 210.0, 'gio': 5.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 2},
    'C-D':   {'km': 235.0, 'gio': 6.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 0},
    'C-E':   {'km': 275.0, 'gio': 8.0,  'dem': 1.0, 'tudong_min': 0.0, 'days': 0},
}

class StudentDatabase:
    def __init__(self):
        self._lock = threading.RLock()
        self._stats_cache = None
        self.version = 1
        self.last_modified = time.time()
        self.roster = self._load_roster()
        self.students = self._load()
        self._sync_students_from_roster()
        self.recalculate_all()

    def _sync_students_from_roster(self):
        """
        Đảm bảo CSDL chỉ chứa học viên do giáo viên nhập (từ danh sách gốc).
        Xóa bỏ toàn bộ học viên lạ khác của trung tâm, đồng thời giữ nguyên kết quả đã tra cứu.
        """
        if not self.roster:
            # Tuyệt đối không xóa trắng self.students nếu roster rỗng để bảo vệ dữ liệu
            return

        roster_keys = set()
        for r_key, r_st in self.roster.items():
            cccd = r_st.get('cccd', '').strip()
            mahv = r_st.get('mahv', '').strip()
            hoten = r_st.get('hoten', '').strip()
            key = mahv or cccd or hoten
            if not key: continue
            roster_keys.add(key)
            if cccd: roster_keys.add(cccd)
            if mahv: roster_keys.add(mahv)
            if hoten: roster_keys.add(strip_vietnamese_accents(hoten))

        cleaned_students = {}
        for s_key, s_st in self.students.items():
            s_cccd = s_st.get('cccd', '').strip()
            s_mahv = s_st.get('mahv', '').strip()
            s_hoten = strip_vietnamese_accents(s_st.get('hoten', ''))
            # Giữ lại học viên nếu thuộc danh sách quản lý (Roster) HOẶC do chính tôi tra cứu
            is_mine = (s_key in roster_keys or 
                       (s_cccd and s_cccd in roster_keys) or 
                       (s_mahv and s_mahv in roster_keys) or 
                       (s_hoten and s_hoten in roster_keys) or
                       s_st.get('is_my_query') or
                       s_st.get('quangduong', 0) > 0 or
                       s_st.get('thoigian', 0) > 0 or
                       s_st.get('has_lythuyet') or
                       s_st.get('interaction_username') == 'tranthang252' or
                       s_st.get('interaction_user_id') in ('1380376964304474173', '1380096710386384937'))
            if is_mine:
                key = s_mahv or s_cccd or s_st.get('hoten')
                cleaned_students[key] = s_st

        # Nạp các học viên trong roster vào students nếu chưa có (khởi tạo với các chỉ số ban đầu)
        now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        for r_key, r_st in self.roster.items():
            cccd = r_st.get('cccd', '').strip()
            mahv = r_st.get('mahv', '').strip()
            hoten = r_st.get('hoten', '').strip()
            target_key = mahv or cccd or hoten
            if not target_key: continue

            existing = None
            if target_key in cleaned_students:
                existing = cleaned_students[target_key]
            else:
                for k, s in cleaned_students.items():
                    if ((cccd and s.get('cccd') == cccd) or 
                        (mahv and s.get('mahv') == mahv) or 
                        (hoten and strip_vietnamese_accents(s.get('hoten')) == strip_vietnamese_accents(hoten))):
                        existing = s
                        break

            if existing:
                for field in ['hoten', 'cccd', 'ngaysinh', 'khoa', 'hang', 'sdt']:
                    if r_st.get(field) and not existing.get(field):
                        existing[field] = r_st.get(field)
                # Đồng bộ ngược lại các trường đã có từ CSDL (đặc biệt là Mã HV và CCCD) vào Roster
                for field in ['mahv', 'cccd', 'khoa', 'hang', 'ngaysinh']:
                    if existing.get(field) and not r_st.get(field):
                        r_st[field] = existing.get(field)
                cleaned_students[target_key] = self.calculate_progress(existing)
            else:
                new_st = {
                    'hoten': hoten,
                    'cccd': cccd,
                    'mahv': mahv,
                    'ngaysinh': r_st.get('ngaysinh', ''),
                    'khoa': r_st.get('khoa', ''),
                    'hang': r_st.get('hang', ''),
                    'sdt': r_st.get('sdt', ''),
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
                    'cabin_uploaded': r_st.get('cabin_uploaded', False),
                    'cabin_status': r_st.get('cabin_status', ''),
                    'cabin_image_url': r_st.get('cabin_image_url', ''),
                    'so_phien_hople': r_st.get('so_phien_hople', 0),
                    'so_phien_loi': r_st.get('so_phien_loi', 0),
                    'phien_status': r_st.get('phien_status', 'Chưa tra phiên'),
                    'phien_loi_detail': r_st.get('phien_loi_detail', []),
                    'phien_recoverable_cars': r_st.get('phien_recoverable_cars', []),
                    'phien_permanent_reasons': r_st.get('phien_permanent_reasons', []),
                    'has_lythuyet': False,
                    'lythuyet_pdf_url': None,
                    'pdf_url': None,
                    'ketqua': 'Chưa tra DAT',
                    'created_at': r_st.get('created_at', now_str),
                    'updated_at': r_st.get('updated_at', now_str),
                    'note': ''
                }
                cleaned_students[target_key] = self.calculate_progress(new_st)

        # Khử trùng lặp học viên theo CCCD và Mã HV
        unique_students = {}
        for s in cleaned_students.values():
            matched_k = None
            for uk, u in unique_students.items():
                if (s.get('cccd') and u.get('cccd') == s.get('cccd')) or (s.get('mahv') and u.get('mahv') == s.get('mahv')):
                    matched_k = uk
                    break
            if matched_k:
                u = unique_students[matched_k]
                for f, v in s.items():
                    if v and not u.get(f):
                        u[f] = v
                if s.get('quangduong', 0) > u.get('quangduong', 0):
                    u['quangduong'] = s['quangduong']
                if s.get('thoigian', 0) > u.get('thoigian', 0):
                    u['thoigian'] = s['thoigian']
                if s.get('thoigian_dem', 0) > u.get('thoigian_dem', 0):
                    u['thoigian_dem'] = s['thoigian_dem']
                if s.get('thoigian_tudong', 0) > u.get('thoigian_tudong', 0):
                    u['thoigian_tudong'] = s['thoigian_tudong']
                if s.get('songay_str') and s.get('songay_str') not in ('', '0/5 ngày'):
                    u['songay_str'] = s['songay_str']
                if s.get('lich_trinh_ngay') and len(s.get('lich_trinh_ngay', [])) > len(u.get('lich_trinh_ngay', [])):
                    u['lich_trinh_ngay'] = s['lich_trinh_ngay']
                if s.get('songay_dat'):
                    u['songay_dat'] = True
                if s.get('pdf_url') and not u.get('pdf_url'):
                    u['pdf_url'] = s['pdf_url']
                if s.get('lythuyet_pdf_url') and not u.get('lythuyet_pdf_url'):
                    u['lythuyet_pdf_url'] = s['lythuyet_pdf_url']
                unique_students[matched_k] = self.calculate_progress(u)
            else:
                canonical_k = s.get('mahv') or s.get('cccd') or s.get('hoten')
                unique_students[canonical_k] = self.calculate_progress(s)

        self.students = unique_students
        self._save()

    def recalculate_all(self):
        """Tính toán lại tất cả các chỉ số theo định mức chuẩn mới cập nhật."""
        for k, s in list(self.students.items()):
            self.students[k] = self.calculate_progress(s)
        self._save()

    def purge_foreign_students(self):
        """
        Chỉ giữ lại những học viên có trong hồ sơ gốc (Roster)
        hoặc do chính giáo viên này tra cứu. Xóa sạch mọi học viên lạ bị lẫn từ kênh chung.
        """
        roster_keys = set()
        for r_key, r_st in self.roster.items():
            cccd = (r_st.get('cccd') or '').strip().lower()
            mahv = (r_st.get('mahv') or '').strip().lower()
            hoten = (r_st.get('hoten') or r_key or '').strip()
            if hoten:
                roster_keys.add(hoten.lower())
                roster_keys.add(strip_vietnamese_accents(hoten).lower())
            if cccd:
                roster_keys.add(cccd)
            if mahv:
                roster_keys.add(mahv)

        cleaned = {}
        for s_key, s in list(self.students.items()):
            s_cccd = (s.get('cccd') or '').strip().lower()
            s_mahv = (s.get('mahv') or '').strip().lower()
            s_hoten = (s.get('hoten') or '').strip().lower()
            s_no_acc = strip_vietnamese_accents(s.get('hoten') or '').lower()

            is_in_roster = (
                (s_hoten and s_hoten in roster_keys) or
                (s_no_acc and s_no_acc in roster_keys) or
                (s_cccd and s_cccd in roster_keys) or
                (s_mahv and s_mahv in roster_keys)
            )
            is_queried_by_me = (
                s.get('is_my_query') or
                s.get('quangduong', 0) > 0 or
                s.get('thoigian', 0) > 0 or
                s.get('has_lythuyet') or
                s.get('interaction_username') == 'tranthang252' or
                s.get('interaction_user_id') in ('1380376964304474173', '1380096710386384937')
            )

            if is_in_roster or is_queried_by_me:
                key = s.get('mahv') or s.get('cccd') or s.get('hoten')
                if key:
                    cleaned[key] = s

        self.students = cleaned
        self._save()
        return len(cleaned)

    def _load_roster(self):
        if os.path.exists(ROSTER_FILE):
            try:
                with open(ROSTER_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_roster(self):
        with self._lock:
            try:
                tmp_file = ROSTER_FILE + '.tmp'
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.roster, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, ROSTER_FILE)
                self._stats_cache = None
                self.version += 1
                self.last_modified = time.time()
                notify_data_changed()
            except Exception as e:
                print("Lỗi lưu danh sách gốc:", e)

    def _load(self):
        if os.path.exists(STUDENTS_FILE):
            try:
                with open(STUDENTS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        with self._lock:
            try:
                tmp_file = STUDENTS_FILE + '.tmp'
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.students, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, STUDENTS_FILE)
                self._stats_cache = None
                self.version += 1
                self.last_modified = time.time()
                notify_data_changed()
                
                # Backup định kỳ theo ngày
                today_str = datetime.now().strftime('%Y%m%d')
                backup_file = os.path.join(BACKUP_DIR, f'students_{today_str}.json')
                if not os.path.exists(backup_file):
                    shutil.copyfile(STUDENTS_FILE, backup_file)
            except Exception as e:
                print("Lỗi lưu cơ sở dữ liệu:", e)

    def save(self):
        """Lưu cơ sở dữ liệu học viên ra đĩa."""
        return self._save()

    def calculate_progress(self, student):
        """Tính toán tỷ lệ hoàn thành % và các chỉ tiêu chuẩn hóa theo hạng xe."""
        hang = student.get('hang', '').strip().upper()
        # Tìm benchmark phù hợp
        bm = BENCHMARKS.get(hang)
        if not bm:
            if 'B.01' in hang or 'B1' in hang: bm = BENCHMARKS['B.01']
            elif 'C1' in hang: bm = BENCHMARKS['C1']
            elif 'CM-CE' in hang: bm = BENCHMARKS['CM-CE']
            elif 'B-CM' in hang or 'CM' in hang or 'LÊN' in hang: bm = BENCHMARKS['B-CM']
            elif 'C' in hang and 'B' not in hang: bm = BENCHMARKS['C1']
            elif 'B' in hang: bm = BENCHMARKS['B']
            else: bm = BENCHMARKS['B']

        target_km = bm['km']
        target_gio = bm['gio']
        dem_min = float(student.get('thoigian_dem_toithieu') or bm.get('dem', 2.0))
        tudong_min = float(student.get('thoigian_tudong_toithieu') if student.get('thoigian_tudong_toithieu') is not None else bm.get('tudong_min', 0.0))

        curr_km = float(student.get('quangduong', 0.0) or 0.0)
        curr_gio = float(student.get('thoigian', 0.0) or 0.0)
        curr_dem = float(student.get('thoigian_dem', 0.0) or 0.0)
        curr_tudong = float(student.get('thoigian_tudong', 0.0) or 0.0)

        pct_km = min(100.0, round((curr_km / target_km) * 100, 1)) if target_km > 0 else 100.0
        pct_gio = min(100.0, round((curr_gio / target_gio) * 100, 1)) if target_gio > 0 else 100.0
        overall_pct = min(100.0, round((pct_km * 0.6) + (pct_gio * 0.4), 1))

        # Đánh giá điều kiện từng trường dữ liệu (Đủ / Thiếu)
        is_km_ok = curr_km >= (target_km - 0.5)
        is_gio_ok = curr_gio >= (target_gio - 0.05)
        is_dem_ok = curr_dem >= (dem_min - 0.05)
        is_tudong_ok = (tudong_min == 0.0) or (curr_tudong >= tudong_min)

        songay_dat = bool(student.get('songay_dat', False))
        songay_str = (student.get('songay_str') or '').strip()
        lich_trinh = student.get('lich_trinh_ngay', []) or []

        # Tự động đồng bộ số ngày từ lịch trình phiên nếu songay_str rỗng hoặc 0/5
        if (not songay_str or songay_str in ('', '0/5 ngày', '0 ngày')) and len(lich_trinh) > 0:
            target_days = 2 if ('CM' in hang or 'LÊN' in hang or 'CE' in hang) else 5
            songay_str = f"{len(lich_trinh)}/{target_days} ngày"
            student['songay_str'] = songay_str

        m_day = re.search(r'(\d+)\s*/\s*(\d+)', songay_str)
        if m_day and int(m_day.group(1)) >= int(m_day.group(2)):
            songay_dat = True

        student['target_km'] = target_km
        student['target_gio'] = target_gio
        student['thoigian_dem_toithieu'] = dem_min
        student['thoigian_tudong_toithieu'] = tudong_min
        student['pct_km'] = pct_km
        student['pct_gio'] = pct_gio
        student['overall_pct'] = overall_pct

        student['quangduong_dat'] = is_km_ok
        student['thoigian_dat'] = is_gio_ok
        student['thoigian_dem_dat'] = is_dem_ok
        student['thoigian_tudong_dat'] = is_tudong_ok
        student['songay_dat'] = songay_dat

        student['quangduong_thieu'] = max(0.0, round(target_km - curr_km, 1))
        student['thoigian_thieu'] = max(0.0, round(target_gio - curr_gio, 1))
        student['thoigian_dem_thieu'] = max(0.0, round(dem_min - curr_dem, 1))
        student['thoigian_tudong_thieu'] = max(0.0, round(tudong_min - curr_tudong, 1))

        student['has_lythuyet'] = bool(student.get('lythuyet_pdf_url') or student.get('has_lythuyet'))
        student['has_phien_pdf'] = bool(student.get('pdf_url'))

        # Đánh giá trạng thái phiên học (Phiên hợp lệ, Lỗi vĩnh viễn, hoặc Chờ đồng bộ)
        if student.get('has_permanent_error') or student.get('phien_permanent_reasons'):
            student['phien_status'] = 'Lỗi vĩnh viễn (Y/c chạy bù)'
        elif student.get('phien_recoverable_cars'):
            cars_str = ", ".join(student.get('phien_recoverable_cars', []))
            student['phien_status'] = f"Chờ đồng bộ ({cars_str})"
        elif student.get('so_phien_loi', 0) > 0:
            student['phien_status'] = f"Có {student.get('so_phien_loi')} phiên lỗi"
        elif student.get('so_phien_hople', 0) > 0:
            student['phien_status'] = 'Hợp lệ'
        elif not student.get('phien_status'):
            student['phien_status'] = 'Chưa tra phiên'

        # Đánh giá lại kết quả tổng DAT
        if is_km_ok and is_gio_ok and is_dem_ok and is_tudong_ok and songay_dat:
            student['ketqua'] = 'Đạt'
        elif curr_km == 0 and curr_gio == 0 and not songay_dat:
            student['ketqua'] = 'Chưa tra DAT'
        else:
            student['ketqua'] = 'Chưa Đạt'

        # Đánh giá điều kiện thi tốt nghiệp từ PDF lý thuyết
        try:
            import totnghiep_service
            downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
            student = totnghiep_service.evaluate_student(student, downloads_dir)
        except Exception as e:
            student['dieukien_totnghiep'] = student.get('dieukien_totnghiep', 'Chưa tra LT')

        return student

    def upsert_student(self, student_data, auto_save=True):
        if not student_data:
            return None

        mahv = student_data.get('mahv', '').strip()
        cccd = student_data.get('cccd', '').strip()
        hoten = student_data.get('hoten', '').strip()
        if student_data.get('is_my_query') is None:
            student_data['is_my_query'] = True

        # 1. Tìm key hiện có qua MAHV, CCCD hoặc Họ tên
        existing_key = None
        if mahv and mahv in self.students:
            existing_key = mahv
        elif cccd and cccd in self.students:
            existing_key = cccd
        else:
            for k, s in self.students.items():
                if (mahv and s.get('mahv') == mahv) or (cccd and s.get('cccd') == cccd):
                    existing_key = k
                    break
            if not existing_key and hoten:
                for k, s in self.students.items():
                    if s.get('hoten', '').strip().lower() == hoten.lower():
                        existing_key = k
                        break

        now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        if existing_key:
            target_key = mahv if mahv else existing_key
            existing = self.students.pop(existing_key) if (target_key != existing_key and target_key) else self.students[existing_key]

            for k, v in student_data.items():
                if v or (v is not False and v == 0):
                    if k == 'lich_trinh_ngay' and not v and existing.get('lich_trinh_ngay'):
                        continue
                    if k == 'note' and not v and existing.get('note'):
                        continue
                    # Không ghi đè quãng đường/thời gian nếu dữ liệu mới là rỗng hoặc 0 từ lệnh lý thuyết
                    if k in ('quangduong', 'thoigian', 'thoigian_dem', 'thoigian_tudong') and v == 0 and existing.get(k, 0) > 0:
                        continue
                    # Không ghi đè số ngày nếu dữ liệu mới rỗng hoặc 0/5 trong khi dữ liệu cũ đã có
                    if k == 'songay_str' and (not v or v in ('', '0/5 ngày')) and existing.get('songay_str') and existing.get('songay_str') not in ('', '0/5 ngày'):
                        continue
                    if k == 'songay_dat' and not v and existing.get('songay_dat') is True:
                        continue
                    existing[k] = v

            existing['updated_at'] = now_str
            self.students[target_key] = self.calculate_progress(existing)

            # Tự động cập nhật Mã HV & CCCD vào Roster ban đầu để nhớ tên tra cứu cho lần sau
            if hoten or mahv or cccd:
                norm_name = strip_vietnamese_accents(hoten)
                roster_mod = False
                matched_rk = None
                for rk, r in self.roster.items():
                    r_norm = strip_vietnamese_accents(r.get('hoten', ''))
                    if (mahv and r.get('mahv') == mahv) or (cccd and r.get('cccd') == cccd) or (norm_name and r_norm == norm_name):
                        matched_rk = rk
                        break
                if matched_rk:
                    r = self.roster[matched_rk]
                    if mahv and r.get('mahv') != mahv: r['mahv'] = mahv; roster_mod = True
                    if cccd and r.get('cccd') != cccd: r['cccd'] = cccd; roster_mod = True
                    if student_data.get('khoa') and not r.get('khoa'): r['khoa'] = student_data.get('khoa'); roster_mod = True
                    if student_data.get('hang') and not r.get('hang'): r['hang'] = student_data.get('hang'); roster_mod = True
                else:
                    self.roster[hoten or mahv or cccd] = {
                        'hoten': hoten,
                        'cccd': cccd,
                        'mahv': mahv,
                        'khoa': student_data.get('khoa', ''),
                        'hang': student_data.get('hang', ''),
                        'ngaysinh': student_data.get('ngaysinh', '')
                    }
                    roster_mod = True
                if roster_mod:
                    self._save_roster()

            if auto_save: self._save()
            return self.students[target_key]
        else:
            key = mahv or cccd or hoten
            if not key:
                return None
            student_data['created_at'] = now_str
            student_data['updated_at'] = now_str
            student_data['note'] = student_data.get('note', '')
            self.students[key] = self.calculate_progress(student_data)

            # Tự động cập nhật Mã HV & CCCD vào Roster ban đầu
            if hoten or mahv or cccd:
                norm_name = strip_vietnamese_accents(hoten)
                roster_mod = False
                matched_rk = None
                for rk, r in self.roster.items():
                    r_norm = strip_vietnamese_accents(r.get('hoten', ''))
                    if (mahv and r.get('mahv') == mahv) or (cccd and r.get('cccd') == cccd) or (norm_name and r_norm == norm_name):
                        matched_rk = rk
                        break
                if matched_rk:
                    r = self.roster[matched_rk]
                    if mahv and r.get('mahv') != mahv: r['mahv'] = mahv; roster_mod = True
                    if cccd and r.get('cccd') != cccd: r['cccd'] = cccd; roster_mod = True
                else:
                    self.roster[hoten or mahv or cccd] = {
                        'hoten': hoten,
                        'cccd': cccd,
                        'mahv': mahv,
                        'khoa': student_data.get('khoa', ''),
                        'hang': student_data.get('hang', ''),
                        'ngaysinh': student_data.get('ngaysinh', '')
                    }
                    roster_mod = True
                if roster_mod:
                    self._save_roster()

            if auto_save: self._save()
            return self.students[key]

    def upsert_many(self, students_list):
        count = 0
        for s in students_list:
            if self.upsert_student(s, auto_save=False):
                count += 1
        if count > 0:
            self._save()
        return count

    def find_student(self, identifier):
        if not identifier: return None
        q = str(identifier).strip().lower()
        if q in self.students:
            return self.students[q]
        for s in self.students.values():
            if (s.get('mahv', '').lower() == q or 
                s.get('cccd', '').lower() == q or 
                s.get('hoten', '').strip().lower() == q):
                return s
        return None

    def update_note(self, student_id, note_text):
        if student_id in self.students:
            self.students[student_id]['note'] = note_text.strip()
            self.students[student_id]['updated_at'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            self._save()
            return True
        return False

    def get_all(self, keyword=None, status_filter='all', course_filter='all', sort_by='updated'):
        with self._lock:
            results = list(self.students.values())

        # Lọc theo từ khóa (Tên, CCCD, Mã HV, Khóa, Ghi chú)
        if keyword:
            kw = keyword.lower().strip()
            results = [
                s for s in results
                if (kw in s.get('hoten', '').lower() or
                    kw in s.get('mahv', '').lower() or
                    kw in s.get('cccd', '').lower() or
                    kw in s.get('khoa', '').lower() or
                    kw in s.get('note', '').lower())
            ]

        # Lọc theo khóa học
        if course_filter and course_filter != 'all':
            results = [s for s in results if s.get('khoa') == course_filter]

        # Lọc theo trạng thái
        if status_filter == 'pass':
            results = [s for s in results if s.get('ketqua') == 'Đạt']
        elif status_filter == 'fail_day':
            results = [s for s in results if s.get('songay_dat') is False and s.get('quangduong_dat') is True]
        elif status_filter == 'fail_km':
            results = [s for s in results if s.get('quangduong_dat') is False or s.get('thoigian_dat') is False]
        elif status_filter == 'fail_all':
            results = [s for s in results if s.get('ketqua') != 'Đạt']
        elif status_filter == 'totnghiep_pass':
            results = [s for s in results if s.get('dieukien_totnghiep') == 'Đạt']
        elif status_filter == 'totnghiep_fail':
            results = [s for s in results if s.get('dieukien_totnghiep') == 'Không']

        # Sắp xếp
        if sort_by == 'name':
            results.sort(key=lambda x: x.get('hoten', '').split()[-1] if x.get('hoten') else '')
        elif sort_by == 'km_desc':
            results.sort(key=lambda x: x.get('quangduong', 0.0), reverse=True)
        elif sort_by == 'pct_desc':
            results.sort(key=lambda x: x.get('overall_pct', 0.0), reverse=True)
        else:
            results.sort(key=lambda x: x.get('updated_at', ''), reverse=True)

        return results

    def get_by_id(self, student_id):
        if not student_id:
            return None
        sid = str(student_id).strip()
        if sid in self.students:
            return self.students[sid]
        sid_norm = strip_vietnamese_accents(sid)
        for s in self.students.values():
            if str(s.get('mahv', '')).strip() == sid or str(s.get('cccd', '')).strip() == sid:
                return s
            name_raw = str(s.get('hoten', '')).strip()
            if name_raw.lower() == sid.lower() or strip_vietnamese_accents(name_raw) == sid_norm:
                return s
        return None

    def delete_student(self, student_id):
        """
        Xóa triệt để học viên khỏi cả CSDL kết quả (students) VÀ Danh sách gốc (roster).
        Đảm bảo học viên đã xóa không bao giờ bị tự động nạp lại khi khởi động hay đồng bộ.
        """
        if not student_id:
            return False
        sid = str(student_id).strip()
        deleted = False
        target_student = None

        # 1. Tìm thông tin học viên trước khi xóa để gom đủ cccd, mahv, hoten
        if sid in self.students:
            target_student = self.students[sid]
        else:
            sid_norm = strip_vietnamese_accents(sid)
            for k, s in list(self.students.items()):
                if str(s.get('mahv', '')).strip() == sid or str(s.get('cccd', '')).strip() == sid:
                    target_student = s
                    break
                name_raw = str(s.get('hoten', '')).strip()
                if name_raw.lower() == sid.lower() or strip_vietnamese_accents(name_raw) == sid_norm:
                    target_student = s
                    break

        keys_to_purge = {sid.lower()}
        sid_norm = strip_vietnamese_accents(sid).lower()
        if sid_norm:
            keys_to_purge.add(sid_norm)

        if target_student:
            if target_student.get('mahv'):
                keys_to_purge.add(str(target_student['mahv']).strip().lower())
            if target_student.get('cccd'):
                keys_to_purge.add(str(target_student['cccd']).strip().lower())
            if target_student.get('hoten'):
                h = str(target_student['hoten']).strip().lower()
                keys_to_purge.add(h)
                keys_to_purge.add(strip_vietnamese_accents(h))

        # 2. Xóa khỏi self.students
        for k, s in list(self.students.items()):
            k_low = str(k).strip().lower()
            k_norm = strip_vietnamese_accents(k_low)
            s_mahv = str(s.get('mahv', '')).strip().lower()
            s_cccd = str(s.get('cccd', '')).strip().lower()
            s_hoten = str(s.get('hoten', '')).strip().lower()
            s_norm = strip_vietnamese_accents(s_hoten)
            if (k_low in keys_to_purge or k_norm in keys_to_purge or
                (s_mahv and s_mahv in keys_to_purge) or 
                (s_cccd and s_cccd in keys_to_purge) or
                (s_hoten and s_hoten in keys_to_purge) or 
                (s_norm and s_norm in keys_to_purge)):
                del self.students[k]
                deleted = True

        # 3. XÓA TRIỆT ĐỂ KHỎI self.roster (DANH SÁCH GỐC) để không bao giờ bị tự động nạp lại
        roster_deleted = False
        for rk, r in list(self.roster.items()):
            rk_low = str(rk).strip().lower()
            rk_norm = strip_vietnamese_accents(rk_low)
            r_mahv = str(r.get('mahv', '')).strip().lower()
            r_cccd = str(r.get('cccd', '')).strip().lower()
            r_hoten = str(r.get('hoten', '')).strip().lower()
            r_norm = strip_vietnamese_accents(r_hoten)
            if (rk_low in keys_to_purge or rk_norm in keys_to_purge or
                (r_mahv and r_mahv in keys_to_purge) or 
                (r_cccd and r_cccd in keys_to_purge) or
                (r_hoten and r_hoten in keys_to_purge) or 
                (r_norm and r_norm in keys_to_purge)):
                del self.roster[rk]
                roster_deleted = True

        if deleted or roster_deleted:
            self._save()
            if roster_deleted:
                self._save_roster()
            return True
        return False

    def delete_courses(self, course_names):
        """
        Xóa toàn bộ học viên thuộc một hoặc nhiều khóa học đã thi xong.
        Hỗ trợ: chuỗi 1 khóa, chuỗi ngăn cách dấu phẩy, hoặc danh sách list/tuple/set.
        """
        if not course_names:
            return 0
        if isinstance(course_names, str):
            c_list = [c.strip().lower() for c in course_names.split(',') if c.strip()]
        elif isinstance(course_names, (list, tuple, set)):
            c_list = [str(c).strip().lower() for c in course_names if str(c).strip()]
        else:
            c_list = [str(course_names).strip().lower()]

        if not c_list:
            return 0

        target_courses = set(c_list)
        with self._lock:
            # 1. Thu thập danh sách học viên thuộc các khóa cần xóa
            target_keys = set()
            for k, s in list(self.students.items()):
                s_khoa = str(s.get('khoa') or '').strip().lower()
                if s_khoa in target_courses:
                    target_keys.add(str(k).strip().lower())
                    if s.get('mahv'): target_keys.add(str(s['mahv']).strip().lower())
                    if s.get('cccd'): target_keys.add(str(s['cccd']).strip().lower())
                    if s.get('hoten'):
                        target_keys.add(str(s['hoten']).strip().lower())
                        target_keys.add(strip_vietnamese_accents(s['hoten']).lower())

            for rk, r in list(self.roster.items()):
                r_khoa = str(r.get('khoa') or '').strip().lower()
                if r_khoa in target_courses:
                    target_keys.add(str(rk).strip().lower())
                    if r.get('mahv'): target_keys.add(str(r['mahv']).strip().lower())
                    if r.get('cccd'): target_keys.add(str(r['cccd']).strip().lower())
                    if r.get('hoten'):
                        target_keys.add(str(r['hoten']).strip().lower())
                        target_keys.add(strip_vietnamese_accents(r['hoten']).lower())

            count = 0
            # 2. Xóa khỏi self.students
            for k, s in list(self.students.items()):
                k_low = str(k).strip().lower()
                k_norm = strip_vietnamese_accents(k_low)
                s_mahv = str(s.get('mahv') or '').strip().lower()
                s_cccd = str(s.get('cccd') or '').strip().lower()
                s_hoten = str(s.get('hoten') or '').strip().lower()
                s_norm = strip_vietnamese_accents(s_hoten)
                s_khoa = str(s.get('khoa') or '').strip().lower()

                if (s_khoa in target_courses or k_low in target_keys or k_norm in target_keys or
                    s_mahv in target_keys or s_cccd in target_keys or
                    s_hoten in target_keys or s_norm in target_keys):
                    del self.students[k]
                    count += 1

            # 3. Xóa khỏi self.roster
            roster_count = 0
            for rk, r in list(self.roster.items()):
                rk_low = str(rk).strip().lower()
                rk_norm = strip_vietnamese_accents(rk_low)
                r_mahv = str(r.get('mahv') or '').strip().lower()
                r_cccd = str(r.get('cccd') or '').strip().lower()
                r_hoten = str(r.get('hoten') or '').strip().lower()
                r_norm = strip_vietnamese_accents(r_hoten)
                r_khoa = str(r.get('khoa') or '').strip().lower()

                if (r_khoa in target_courses or rk_low in target_keys or rk_norm in target_keys or
                    r_mahv in target_keys or r_cccd in target_keys or
                    r_hoten in target_keys or r_norm in target_keys):
                    del self.roster[rk]
                    roster_count += 1

            if count > 0 or roster_count > 0:
                self._save()
                self._save_roster()
                self._stats_cache = None
                self.version += 1
                self.last_modified = time.time()
                notify_data_changed()

            return count if count > 0 else roster_count

    def delete_course(self, course_name):
        return self.delete_courses(course_name)

    def clear_all(self):
        self.students = {}
        self._save()

    def get_statistics(self):
        with self._lock:
            if self._stats_cache is not None:
                return dict(self._stats_cache)
            students_list = list(self.students.values())

        total = len(students_list)
        passed = sum(1 for s in students_list if s.get('ketqua') == 'Đạt')
        missing_days = sum(1 for s in students_list if s.get('songay_dat') is False and s.get('quangduong_dat') is True)
        missing_km = sum(1 for s in students_list if s.get('quangduong_dat') is False or s.get('thoigian_dat') is False)
        totnghiep_passed = sum(1 for s in students_list if s.get('dieukien_totnghiep') == 'Đạt')
        totnghiep_failed = sum(1 for s in students_list if s.get('dieukien_totnghiep') == 'Không')

        # One-pass grouping for courses and total_km (siêu tốc khi nhiều truy cập)
        total_km = 0.0
        course_buckets = {}
        for s in students_list:
            km = float(s.get('quangduong', 0.0) or 0.0)
            total_km += km
            c = s.get('khoa')
            if c:
                if c not in course_buckets:
                    course_buckets[c] = {'total': 0, 'passed': 0, 'totnghiep_passed': 0, 'km_sum': 0.0}
                b = course_buckets[c]
                b['total'] += 1
                if s.get('ketqua') == 'Đạt':
                    b['passed'] += 1
                if s.get('dieukien_totnghiep') == 'Đạt':
                    b['totnghiep_passed'] += 1
                b['km_sum'] += km

        courses = sorted(list(course_buckets.keys()))
        course_stats = {}
        for c in courses:
            b = course_buckets[c]
            cnt = max(1, b['total'])
            course_stats[c] = {
                'total': b['total'],
                'passed': b['passed'],
                'totnghiep_passed': b['totnghiep_passed'],
                'avg_km': round(b['km_sum'] / cnt, 1)
            }

        res = {
            'total': total,
            'passed': passed,
            'missing_days': missing_days,
            'missing_km': missing_km,
            'totnghiep_passed': totnghiep_passed,
            'totnghiep_failed': totnghiep_failed,
            'courses': courses,
            'course_stats': course_stats,
            'total_km': round(total_km, 2)
        }
        with self._lock:
            self._stats_cache = res
        return res

    # =========================================================================
    # QUẢN LÝ DANH SÁCH HỌC VIÊN BAN ĐẦU (MASTER ROSTER & SMART RESOLUTION)
    # =========================================================================

    def add_to_roster(self, student_data, auto_save=True):
        """Thêm hoặc cập nhật một học viên vào Danh Sách Học Viên Ban Đầu."""
        if not student_data: return None
        
        hoten = str(student_data.get('hoten') or '').strip()
        cccd = re.sub(r'\D', '', str(student_data.get('cccd') or ''))
        mahv = str(student_data.get('mahv') or '').strip()
        ngaysinh = str(student_data.get('ngaysinh') or '').strip()
        hang = str(student_data.get('hang') or '').strip().upper()
        khoa = str(student_data.get('khoa') or '').strip()
        sdt = str(student_data.get('sdt') or '').strip()
        ghichu = str(student_data.get('ghichu') or student_data.get('note') or '').strip()

        if not hoten and not cccd and not mahv:
            return None

        # Khóa định danh ưu tiên: CCCD > Mã HV > Họ tên
        key = cccd or mahv or hoten
        now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        # Kiểm tra xem đã có học viên này trong danh sách gốc chưa
        existing_key = None
        if key in self.roster:
            existing_key = key
        else:
            for k, s in self.roster.items():
                if (cccd and s.get('cccd') == cccd) or (mahv and s.get('mahv') == mahv):
                    existing_key = k
                    break
                elif hoten and strip_vietnamese_accents(s.get('hoten')) == strip_vietnamese_accents(hoten):
                    existing_key = k
                    break

        target_key = key
        if existing_key and existing_key in self.roster:
            st = self.roster.pop(existing_key) if existing_key != target_key else self.roster[existing_key]
            st['hoten'] = hoten or st.get('hoten', '')
            st['cccd'] = cccd or st.get('cccd', '')
            st['mahv'] = mahv or st.get('mahv', '')
            st['ngaysinh'] = ngaysinh or st.get('ngaysinh', '')
            st['hang'] = hang or st.get('hang', '')
            st['khoa'] = khoa or st.get('khoa', '')
            st['sdt'] = sdt or st.get('sdt', '')
            st['ghichu'] = ghichu or st.get('ghichu', '')
            st['updated_at'] = now_str
            self.roster[target_key] = st
        else:
            st = {
                'hoten': hoten,
                'cccd': cccd,
                'mahv': mahv,
                'ngaysinh': ngaysinh,
                'hang': hang,
                'khoa': khoa,
                'sdt': sdt,
                'ghichu': ghichu,
                'created_at': now_str,
                'updated_at': now_str
            }
            self.roster[target_key] = st

        if auto_save:
            self._save_roster()
            self._sync_students_from_roster()
        return self.roster[target_key]

    def import_roster_from_excel(self, filepath):
        """
        Nhập danh sách học viên gốc từ tệp Excel (.xlsx).
        Tự động nhận diện các cột: Họ và tên, Số CCCD, Ngày sinh, Hạng, Khóa, SĐT.
        """
        import openpyxl
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        
        headers = {}
        header_row_idx = None
        
        # 1. Tìm dòng tiêu đề
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if r_idx > 15: break # Tiêu đề thường nằm trong 15 dòng đầu
            row_vals = [strip_vietnamese_accents(str(c or '')) for c in row]
            for c_idx, val in enumerate(row_vals):
                if any(k in val for k in ['ho va ten', 'ho ten', 'ten hoc vien', 'hoc vien', 'full name']):
                    headers['hoten'] = c_idx
                elif any(k in val for k in ['cccd', 'cmnd', 'can cuoc', 'dinh danh']):
                    headers['cccd'] = c_idx
                elif any(k in val for k in ['ngay sinh', 'ngaysinh', 'nam sinh', 'dob', 'birth']):
                    headers['ngaysinh'] = c_idx
                elif any(k in val for k in ['khoa', 'khoa hoc', 'lop']):
                    headers['khoa'] = c_idx
                elif any(k in val for k in ['hang', 'hang xe', 'hang dao tao']):
                    headers['hang'] = c_idx
                elif any(k in val for k in ['mahv', 'ma hoc vien', 'mshv', 'ma so']):
                    headers['mahv'] = c_idx
                elif any(k in val for k in ['sdt', 'dien thoai', 'so dien thoai', 'phone']):
                    headers['sdt'] = c_idx
                    
            if 'hoten' in headers or 'cccd' in headers:
                header_row_idx = r_idx
                break

        if header_row_idx is None:
            # Fallback nếu không có hàng tiêu đề: Giả định cột 1 là Họ tên, cột 2 là CCCD hoặc ngày sinh
            header_row_idx = 0
            headers = {'hoten': 0, 'cccd': 1, 'ngaysinh': 2}

        added = []
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if r_idx <= header_row_idx: continue
            if not row or all(c is None for c in row): continue

            get_val = lambda col_key: str(row[headers[col_key]]).strip() if col_key in headers and headers[col_key] < len(row) and row[headers[col_key]] is not None else ''

            hoten = get_val('hoten')
            cccd = get_val('cccd')
            ngaysinh = get_val('ngaysinh')
            khoa = get_val('khoa')
            hang = get_val('hang')
            mahv = get_val('mahv')
            sdt = get_val('sdt')

            # Làm sạch ngày sinh nếu Excel đọc ra đối tượng datetime
            if isinstance(row[headers.get('ngaysinh', 0)] if 'ngaysinh' in headers and headers['ngaysinh'] < len(row) else None, datetime):
                ngaysinh = row[headers['ngaysinh']].strftime('%d/%m/%Y')

            # Nếu họ tên chứa số (ví dụ bị đảo cột)
            if re.search(r'\d{9,12}', hoten) and not cccd:
                cccd, hoten = hoten, cccd

            st = self.add_to_roster({
                'hoten': hoten,
                'cccd': cccd,
                'ngaysinh': ngaysinh,
                'khoa': khoa,
                'hang': hang,
                'mahv': mahv,
                'sdt': sdt
            }, auto_save=False)
            if st:
                added.append(st)

        if added:
            self._save_roster()
            self._sync_students_from_roster()
        return {'success': True, 'count': len(added), 'students': added}

    def import_roster_from_text(self, raw_text):
        """
        Nhập danh sách học viên từ văn bản copy/paste (hỗ trợ định dạng Excel copy Tab, Dấu phẩy, hoặc Gạch đứng).
        Tự động phân tách chính xác Họ tên, CCCD, Ngày sinh, Khóa học, Hạng đào tạo và SĐT.
        """
        if not raw_text: return {'success': False, 'count': 0, 'students': []}
        lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
        added = []

        for line in lines:
            # Bỏ qua dòng tiêu đề
            lower_l = strip_vietnamese_accents(line)
            if any(lower_l.startswith(k) for k in ['ho va ten', 'stt', 'danh sach', 'ho ten']):
                continue

            parts = [p.strip() for p in re.split(r'[\t|,;]', line) if p.strip()]
            
            hoten = ''
            cccd = ''
            mahv = ''
            dob = ''
            khoa = ''
            hang = ''
            sdt = ''

            if len(parts) >= 2:
                # Phân tích từng cột riêng biệt
                remaining_parts = []
                for p in parts:
                    clean_p = p.strip()
                    # Check Mã HV
                    m_m = re.search(r'(52001-\d{8}-\d{6})', clean_p)
                    if m_m and not mahv:
                        mahv = m_m.group(1)
                        continue
                    # Check CCCD (12 hoặc 9 số)
                    m_c = re.search(r'(?<!\d)(0\d{11}|[0-3]\d{8})(?!\d)', clean_p)
                    if m_c and not cccd:
                        cccd = m_c.group(1)
                        continue
                    # Check Ngày sinh
                    m_d = re.search(r'(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})', clean_p)
                    if m_d and not dob:
                        dob = m_d.group(1).replace('-', '/').replace('.', '/')
                        continue
                    # Check Khóa / Hạng (VD: B2-K24, C-K10, B1-K15, B2K24...)
                    m_k = re.search(r'^(B11?|B2|C|D|E|FC|FE)[\s\-_]*(K\d+)?$|^K\d+$', clean_p, re.IGNORECASE)
                    if m_k and not khoa:
                        khoa = clean_p.upper()
                        m_h = re.match(r'^(B11?|B2|C|D|E|FC|FE)', clean_p, re.IGNORECASE)
                        if m_h: hang = m_h.group(1).upper()
                        continue
                    # Check SĐT (10 số bắt đầu bằng 03, 05, 07, 08, 09)
                    m_tel = re.search(r'^(0[35789]\d{8})$', clean_p)
                    if m_tel and not sdt:
                        sdt = m_tel.group(1)
                        continue
                    # Check STT (chỉ có số thứ tự 1, 2, 3...)
                    if re.match(r'^\d{1,4}\.?$', clean_p):
                        continue
                    remaining_parts.append(clean_p)

                # Cột còn lại có chứa chữ cái tiếng Việt là Họ tên
                for p in remaining_parts:
                    p_name = re.sub(r'[^a-zA-ZÀ-ỹ\s]', '', p).strip()
                    if len(p_name.split()) >= 1 and not hoten:
                        hoten = ' '.join(p_name.split()).title()
                        break
            else:
                # Xử lý văn bản tự do 1 dòng không phân tách cột
                m_cccd = re.search(r'(?<!\d)(0\d{11}|[0-3]\d{8})(?!\d)', line)
                if m_cccd: cccd = m_cccd.group(1)

                m_mahv = re.search(r'(52001-\d{8}-\d{6})', line)
                if m_mahv: mahv = m_mahv.group(1)

                m_dob = re.search(r'(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})', line)
                if m_dob: dob = m_dob.group(1).replace('-', '/').replace('.', '/')

                m_k = re.search(r'\b((?:B11?|B2|C|D|E|FC|FE)[\s\-_]*K\d+|K\d+)\b', line, re.IGNORECASE)
                if m_k:
                    khoa = m_k.group(1).upper()
                    m_h = re.match(r'^(B11?|B2|C|D|E|FC|FE)', khoa, re.IGNORECASE)
                    if m_h: hang = m_h.group(1).upper()

                clean_name = line
                if cccd: clean_name = clean_name.replace(cccd, ' ')
                if mahv: clean_name = clean_name.replace(mahv, ' ')
                if dob: clean_name = clean_name.replace(dob, ' ')
                if m_k: clean_name = clean_name.replace(m_k.group(0), ' ')
                clean_name = re.sub(r'^(stt\s*)?\d+\s*[\.\-\)]?\s*', '', clean_name, flags=re.IGNORECASE)
                clean_name = re.sub(r'[^a-zA-ZÀ-ỹ\s]', ' ', clean_name)
                hoten = ' '.join(clean_name.split()).title()

            if hoten or cccd or mahv:
                st = self.add_to_roster({
                    'hoten': hoten,
                    'cccd': cccd,
                    'ngaysinh': dob,
                    'khoa': khoa,
                    'hang': hang,
                    'mahv': mahv,
                    'sdt': sdt
                }, auto_save=False)
                if st:
                    added.append(st)

        if added:
            self._save_roster()
            self._sync_students_from_roster()
        return {'success': True, 'count': len(added), 'students': added}

    def import_roster_from_ocr(self, students_list):
        """Nhập trực tiếp danh sách học viên vừa nhận diện từ ảnh OCR vào danh sách gốc."""
        added = []
        for s in students_list:
            st = self.add_to_roster(s, auto_save=False)
            if st: added.append(st)
        if added:
            self._save_roster()
            self._sync_students_from_roster()
        return {'success': True, 'count': len(added), 'students': added}

    def get_roster(self, keyword=None, course=None):
        """Lấy toàn bộ danh sách học viên ban đầu (kèm lọc theo từ khóa hoặc khóa học)."""
        res = list(self.roster.values())
        if keyword:
            kw = strip_vietnamese_accents(keyword)
            res = [
                s for s in res
                if kw in strip_vietnamese_accents(s.get('hoten', '')) or
                   kw in s.get('cccd', '').lower() or
                   kw in s.get('mahv', '').lower() or
                   kw in s.get('khoa', '').lower()
            ]
        if course and course != 'all':
            res = [s for s in res if s.get('khoa') == course]
        
        # Sắp xếp theo tên tiếng Việt
        res.sort(key=lambda s: strip_vietnamese_accents(s.get('hoten', '')).split()[-1] if s.get('hoten') else '')
        return res

    def get_roster_by_id(self, key):
        return self.roster.get(key)

    def delete_from_roster(self, key):
        """Xóa học viên khỏi danh sách gốc và CSDL một cách đồng bộ."""
        return self.delete_student(key)

    def clear_roster(self):
        self.roster = {}
        self._save_roster()
        self.students = {}
        self._save()
        self._save_roster()

    def resolve_student_identifier(self, query):
        """
        TỰ ĐỘNG NHẬN DIỆN HỌC VIÊN TỪ TÊN (SMART RESOLVER):
        Khi giáo viên chỉ gõ một phần tên, tên không dấu hoặc tên gọi (VD: 'Trí', 'khoi', 'Pham Khoi', 'Vo Tinh'):
        Hệ thống tự động tra trong CSDL học viên và Danh sách Roster,
        trả về học viên khớp nhất KÈM MÃ HỌC VIÊN (mahv) HOẶC CCCD ĐÃ LƯU để gửi lệnh tới Bot Discord.
        """
        if not query:
            return None, 'none'
        q_raw = str(query).strip()
        q_norm = strip_vietnamese_accents(q_raw)
        q_digits = re.sub(r'\D', '', q_raw)

        # Tập hợp tất cả học viên đã có, ƯU TIÊN các bản ghi trong self.students vì đã có Mã HV (mahv) và CCCD
        with self._lock:
            st_values = list(self.students.values())
            roster_values = list(self.roster.values())

        pool = []
        seen = set()

        for s in st_values:
            key = s.get('mahv') or s.get('cccd') or s.get('hoten')
            if key and key not in seen:
                seen.add(key)
                pool.append(s)

        for r in roster_values:
            r_key = r.get('mahv') or r.get('cccd') or r.get('hoten')
            r_norm = strip_vietnamese_accents(r.get('hoten', ''))
            if r_key not in seen and r_norm not in seen:
                seen.add(r_key or r_norm)
                pool.append(r)

        def pick_best(candidates):
            if not candidates:
                return None
            # Ưu tiên tuyệt đối ứng viên đã có Mã HV (mahv) hoặc CCCD
            with_mahv = [c for c in candidates if c.get('mahv')]
            if with_mahv:
                return with_mahv[0]
            with_cccd = [c for c in candidates if c.get('cccd')]
            if with_cccd:
                return with_cccd[0]
            return candidates[0]

        # 1. Khớp theo Số CCCD (12 số hoặc ít nhất 6 số)
        if len(q_digits) >= 6:
            cands = [s for s in pool if s.get('cccd') and q_digits in s.get('cccd', '')]
            best = pick_best(cands)
            if best: return best, 'cccd'

        # 2. Khớp theo Mã học viên dạng 52001-...
        if '52001' in q_raw.lower():
            cands = [s for s in pool if s.get('mahv') and q_raw.lower() in s.get('mahv', '').lower()]
            best = pick_best(cands)
            if best: return best, 'mahv'

        # 3. Khớp chính xác tuyệt đối Họ và tên (kể cả có dấu hay không dấu)
        exact_cands = [s for s in pool if s.get('hoten') and strip_vietnamese_accents(s.get('hoten')) == q_norm]
        best = pick_best(exact_cands)
        if best: return best, 'exact_name'

        # 4. Khớp tên gọi cuối cùng (Tên riêng, VD: 'Khôi', 'Trí', 'Vũ', 'Tính', 'Tuấn', 'Ân')
        first_cands = []
        for s in pool:
            words = strip_vietnamese_accents(s.get('hoten', '')).split()
            if words and words[-1] == q_norm:
                first_cands.append(s)
        best = pick_best(first_cands)
        if best: return best, 'first_name'

        # 5. Khớp từ khóa Họ + Tên (VD: 'Phạm Khôi' khớp 'PHẠM ĐĂNG KHÔI', 'Võ Tính' khớp 'VÕ TRUNG TÍNH')
        q_words = q_norm.split()
        if len(q_words) >= 2:
            subset_cands = []
            for s in pool:
                name_words = strip_vietnamese_accents(s.get('hoten', '')).split()
                if all(qw in name_words for qw in q_words):
                    subset_cands.append(s)
            best = pick_best(subset_cands)
            if best: return best, 'word_subset'

        # 6. Khớp chuỗi con (VD: 'văn vũ' trong 'Dương Văn Vũ')
        sub_cands = [s for s in pool if q_norm in strip_vietnamese_accents(s.get('hoten', ''))]
        best = pick_best(sub_cands)
        if best: return best, 'substring'

        return None, 'none'

    def get_autocomplete_suggestions(self, query, limit=8):
        """
        Cung cấp gợi ý học viên tức thì (Live Autocomplete Suggestions) khi giáo viên đang gõ vào ô tìm kiếm.
        """
        if not query or len(str(query).strip()) == 0:
            return []
        
        q_raw = str(query).strip()
        q_norm = strip_vietnamese_accents(q_raw)
        q_digits = re.sub(r'\D', '', q_raw)
        
        seen_keys = set()
        suggestions = []

        def add_sug(s, source_label):
            key = s.get('cccd') or s.get('mahv') or s.get('hoten')
            if key and key not in seen_keys:
                seen_keys.add(key)
                suggestions.append({
                    'hoten': s.get('hoten', ''),
                    'cccd': s.get('cccd', ''),
                    'mahv': s.get('mahv', ''),
                    'ngaysinh': s.get('ngaysinh', ''),
                    'hang': s.get('hang', ''),
                    'khoa': s.get('khoa', ''),
                    'source': source_label,
                    'has_dat': bool(s.get('quangduong', 0) > 0 or s.get('thoigian', 0) > 0),
                    'has_pdf': bool(s.get('pdf_url')),
                    'has_lt': bool(s.get('lythuyet_pdf_url'))
                })

        # Quét trước trong Danh Sách Ban Đầu (Master Roster)
        for s in self.roster.values():
            name_norm = strip_vietnamese_accents(s.get('hoten', ''))
            words = name_norm.split()
            first_name = words[-1] if words else ''
            
            matched = False
            if q_digits and len(q_digits) >= 3 and q_digits in s.get('cccd', ''):
                matched = True
            elif q_norm and (q_norm == name_norm or q_norm == first_name or q_norm in name_norm or all(w in words for w in q_norm.split())):
                matched = True
            elif q_raw.lower() in s.get('mahv', '').lower():
                matched = True
                
            if matched:
                add_sug(s, 'Danh Sách Gốc')
                if len(suggestions) >= limit:
                    return suggestions

        # Quét tiếp trong Cơ sở dữ liệu đã tra cứu (Students Database)
        for s in self.students.values():
            name_norm = strip_vietnamese_accents(s.get('hoten', ''))
            words = name_norm.split()
            first_name = words[-1] if words else ''
            
            matched = False
            if q_digits and len(q_digits) >= 3 and q_digits in s.get('cccd', ''):
                matched = True
            elif q_norm and (q_norm == name_norm or q_norm == first_name or q_norm in name_norm or all(w in words for w in q_norm.split())):
                matched = True
            elif q_raw.lower() in s.get('mahv', '').lower():
                matched = True
                
            if matched:
                add_sug(s, 'Đã Tra Cứu')
                if len(suggestions) >= limit:
                    break

        return suggestions

