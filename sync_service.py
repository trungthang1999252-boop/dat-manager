"""
sync_service.py - Module Quản lý & Tự Động Hóa Đồng Bộ Phiên Lỗi Lên Tổng Cục (/dongbo)
Dự án: Kiểm tra điều kiện chạy DAT (Trường CĐ Cơ điện - Xây dựng và Nông Lâm Trung Bộ)

Quy tắc nghiệp vụ:
1. NGUYÊN TẮC BẢO MẬT & PHÂN QUYỀN:
   - TUYỆT ĐỐI CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU (Học viên nằm trong danh sách hồ sơ gốc Roster hoặc do chính tôi tra cứu is_my_query == True).
   - Tuyệt đối không can thiệp hay tự động đồng bộ cho học viên lạ của người khác trên kênh chung.

2. Phân tích kết quả /phien hoặc file _phienhoc.pdf:
   - LỖI VĨNH VIỄN (Hoàn toàn không thể đẩy về Tổng cục):
     + Vi phạm thời gian: B-0015 (< 5 phút), B-0017 (> 10 giờ), B-0020 (cách nhau < 15 phút).
     + Trạng thái 'Y/c chạy bù' (Vận tốc TB < 20 km/h hoặc vi phạm thời gian bắt buộc chạy bù).
     + Trạng thái 'Không lên TC' hoặc 'Sở từ chối' (Mã '*').
     + Mã lỗi: 4 (Biển số xe sai định dạng).
     -> Đánh dấu trạng thái 'Lỗi vĩnh viễn (Y/c chạy bù)', TUYỆT ĐỐI KHÔNG gọi /dongbo.
   
   - LỖI CÓ THỂ ĐỒNG BỘ ĐƯỢC:
     + Trạng thái 'Chưa lên TC', lỗi timeout, ngắt kết nối máy chủ (-99, null, 0, 503, B-0010, B-0011, 7, 6, 103...).
     -> Đưa vào Hàng đợi Tự động Đồng bộ ngầm qua lệnh /dongbo.

3. Điều kiện thực thi lệnh /dongbo:
   - Phải đúng mã học viên lỗi (mhv) và trên xe bị lỗi (biensoxe).
   - Chỉ chạy lệnh khi xe cần chạy mã lệnh đó đang ONLINE (kiểm tra qua /xeonline).
   - Thời gian thực hiện lệnh /dongbo cách nhau TỐI THIỂU 10 PHÚT (600 giây).
   - Tự động lặp lại quy trình kiểm tra và đồng bộ tới khi HẾT PHIÊN LỖI.
"""

import os
import re
import json
import time
import threading
from datetime import datetime
from phien_service import analyze_phien_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
QUEUE_FILE = os.path.join(DATA_DIR, 'sync_queue.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

# Cooldown tối thiểu 10 phút giữa 2 lần chạy /dongbo cho cùng 1 xe hoặc học viên
MIN_COOLDOWN_SECONDS = 600  # 10 phút (600 giây)

def clean_plate(plate_str):
    """Làm sạch biển số xe, loại bỏ phần chú thích (tđ), khoảng trắng."""
    if not plate_str:
        return ""
    s = str(plate_str).upper()
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'\[.*?\]', '', s)
    s = re.sub(r'[^A-Z0-9]', '', s)
    if s.endswith('TD') and len(s) > 7:
        s = s[:-2]
    elif s.endswith('T') and len(s) > 7:
        s = s[:-1]
    return s.strip()

class AutoSyncManager:
    """
    Quản lý Hàng đợi & Tiến trình Tự động Đồng bộ Tổng cục (/dongbo) chạy ngầm:
    - CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU.
    - Quản lý danh sách các xe và học viên bị lỗi phiên cần đẩy lên TC.
    - Kiểm tra xe đang Online trước khi chạy lệnh.
    - Giữ khoảng cách tối thiểu 10 phút giữa các lần chạy.
    - Tự động chạy tới khi hết phiên lỗi.
    """
    def __init__(self, discord_service_getter=None, my_student_checker=None):
        self.lock = threading.Lock()
        self.get_ds = discord_service_getter
        self.is_my_student = my_student_checker
        self.queue = self._load_queue()
        self.is_running = False
        self.thread = None
        self.cached_online_cars = {}  # {biensoxe: {'online': bool, 'details': str, 'checked_at': float}}

    def _load_queue(self):
        if os.path.exists(QUEUE_FILE):
            try:
                with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_queue(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ Lỗi lưu sync_queue: {e}")

    def add_to_queue(self, mahv, biensoxe, hoten="", khoa="", error_sessions=None):
        """Thêm học viên và xe bị lỗi phiên vào hàng đợi tự động đồng bộ."""
        if not mahv or not biensoxe:
            return False

        # QUY TẮC: CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
        if self.is_my_student and not self.is_my_student(mahv):
            print(f"🛑 [AutoSync] Từ chối thêm vào hàng đợi: {mahv} không thuộc danh sách học viên tôi đã tra cứu!")
            return False
            
        c_plate = clean_plate(biensoxe)
        task_id = f"{mahv}_{c_plate}"

        with self.lock:
            existing = self.queue.get(task_id)
            now = time.time()
            if existing:
                existing['hoten'] = hoten or existing.get('hoten', '')
                existing['khoa'] = khoa or existing.get('khoa', '')
                existing['error_sessions'] = error_sessions or existing.get('error_sessions', [])
                if existing.get('status') == 'completed':
                    existing['status'] = 'pending'
                existing['updated_at'] = now
            else:
                self.queue[task_id] = {
                    'task_id': task_id,
                    'mahv': str(mahv).strip(),
                    'biensoxe': c_plate,
                    'hoten': hoten,
                    'khoa': khoa,
                    'error_sessions': error_sessions or [],
                    'status': 'pending',  # pending, cooldown_10m, waiting_car_online, syncing, completed
                    'created_at': now,
                    'updated_at': now,
                    'last_attempt': 0,
                    'next_allowed_time': now,
                    'retry_count': 0,
                    'history_logs': []
                }
            self._save_queue()
        return True

    def remove_task(self, task_id):
        with self.lock:
            if task_id in self.queue:
                del self.queue[task_id]
                self._save_queue()
                return True
        return False

    def get_status_summary(self):
        """Lấy toàn bộ danh sách hàng đợi và trạng thái phục vụ hiển thị trên Web."""
        with self.lock:
            now = time.time()
            tasks = []
            for tid, t in self.queue.items():
                cooldown_left = max(0, int(t.get('next_allowed_time', 0) - now))
                item = dict(t)
                item['cooldown_left_seconds'] = cooldown_left
                item['cooldown_left_str'] = f"{cooldown_left // 60}p {cooldown_left % 60}s" if cooldown_left > 0 else "Sẵn sàng"
                tasks.append(item)
            return {
                'total_tasks': len(tasks),
                'tasks': tasks,
                'is_worker_running': self.is_running
            }

    def check_car_online(self, biensoxe, ds=None):
        """Kiểm tra xe có đang kết nối/online hay không qua lệnh /xeonline."""
        if not ds and self.get_ds:
            ds = self.get_ds()
        if not ds:
            return False, "Chưa kết nối Discord Service"

        c_plate = clean_plate(biensoxe)
        now = time.time()

        # Kiểm tra cache 60s
        cached = self.cached_online_cars.get(c_plate)
        if cached and (now - cached['checked_at'] < 60):
            return cached['online'], cached['details']

        try:
            res = ds.check_car_online(c_plate)
            is_on = res.get('online', False)
            details = res.get('details', '')
            self.cached_online_cars[c_plate] = {'online': is_on, 'details': details, 'checked_at': now}
            return is_on, details
        except Exception as e:
            return False, f"Lỗi kiểm tra xe online: {str(e)}"

    def execute_dongbo(self, mahv, biensoxe, ds=None):
        """Gửi lệnh /dongbo mhv:<mahv> biensoxe:<biensoxe> tới bot."""
        # QUY TẮC: CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
        if self.is_my_student and not self.is_my_student(mahv):
            return {'success': False, 'error': f'Học viên {mahv} không thuộc danh sách bạn đã tra cứu hoặc quản lý.'}

        if not ds and self.get_ds:
            ds = self.get_ds()
        if not ds:
            return {'success': False, 'error': 'Chưa kết nối Discord Service'}

        c_plate = clean_plate(biensoxe)
        try:
            print(f"🚀 [AutoSync] Đang gửi /dongbo mhv={mahv} biensoxe={c_plate} (Học viên của tôi)...")
            res = ds.send_dongbo(mahv, c_plate)
            return res
        except Exception as e:
            return {'success': False, 'error': f'Lỗi gửi lệnh dongbo: {str(e)}'}

    def start_worker(self):
        """Khởi động luồng nền xử lý tự động."""
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="AutoDongBoWorker")
        self.thread.start()
        print("✅ [AutoSync] Đã khởi động luồng ngầm Tự động Đồng bộ Tổng cục (/dongbo)!")

    def stop_worker(self):
        self.is_running = False

    def _worker_loop(self):
        """Vòng lặp ngầm kiểm tra hàng đợi định kỳ."""
        while self.is_running:
            try:
                now = time.time()
                tasks_to_process = []
                
                with self.lock:
                    for tid, t in list(self.queue.items()):
                        # Chỉ xử lý các tác vụ chưa hoàn thành
                        if t.get('status') in ('pending', 'cooldown_10m', 'waiting_car_online'):
                            tasks_to_process.append(dict(t))

                ds = self.get_ds() if self.get_ds else None

                for t in tasks_to_process:
                    if not self.is_running:
                        break
                    tid = t['task_id']
                    mahv = t['mahv']
                    biensoxe = t['biensoxe']
                    next_time = t.get('next_allowed_time', 0)

                    # KIỂM TRA BẢO MẬT: CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
                    if self.is_my_student and not self.is_my_student(mahv):
                        print(f"🛑 [AutoSync] Loại bỏ {mahv} khỏi hàng đợi vì không phải học viên tôi đã tra cứu!")
                        with self.lock:
                            if tid in self.queue:
                                del self.queue[tid]
                                self._save_queue()
                        continue

                    # 1. KIỂM TRA ĐIỀU KIỆN 10 PHÚT
                    if now < next_time:
                        left = int(next_time - now)
                        with self.lock:
                            if tid in self.queue:
                                self.queue[tid]['status'] = 'cooldown_10m'
                                self.queue[tid]['log'] = f"Đang chờ giãn cách 10 phút (còn {left // 60}p {left % 60}s)"
                        continue

                    # 2. KIỂM TRA ĐIỀU KIỆN XE ONLINE
                    is_online, details = self.check_car_online(biensoxe, ds=ds)
                    if not is_online:
                        with self.lock:
                            if tid in self.queue:
                                self.queue[tid]['status'] = 'waiting_car_online'
                                self.queue[tid]['log'] = f"Xe {biensoxe} đang offline/tắt máy ({details}). Chờ xe nổ máy online..."
                        continue

                    # 3. ĐỦ ĐIỀU KIỆN: THỰC THI /dongbo
                    with self.lock:
                        if tid in self.queue:
                            self.queue[tid]['status'] = 'syncing'
                            self.queue[tid]['log'] = f"Xe {biensoxe} đang online! Đang gửi lệnh /dongbo..."

                    res = self.execute_dongbo(mahv, biensoxe, ds=ds)
                    now_exec = time.time()
                    log_entry = f"[{datetime.now().strftime('%H:%M:%S')}] {res.get('message') or res.get('error')}"

                    with self.lock:
                        if tid in self.queue:
                            self.queue[tid]['last_attempt'] = now_exec
                            self.queue[tid]['next_allowed_time'] = now_exec + MIN_COOLDOWN_SECONDS
                            self.queue[tid]['retry_count'] = self.queue[tid].get('retry_count', 0) + 1
                            self.queue[tid]['status'] = 'cooldown_10m'
                            self.queue[tid]['log'] = log_entry
                            self.queue[tid].setdefault('history_logs', []).append(log_entry)
                            self._save_queue()

                    # 4. CHỜ 50 GIÂY RỒI KIỂM TRA LẠI /phien XEM ĐÃ HẾT LỖI CHƯA
                    time.sleep(50)
                    try:
                        if ds:
                            print(f"🔍 [AutoSync] Đang kiểm tra lại /phien cho học viên {mahv}...")
                            q_res = ds.query_student(mahv, command='phien', timeout_sec=25)
                            if q_res.get('success'):
                                raw_d = q_res.get('data')
                                st_obj = raw_d[0] if isinstance(raw_d, list) and raw_d else (raw_d or {})
                                p_url = st_obj.get('pdf_url')
                                if p_url:
                                    p_analysis = analyze_phien_pdf(p_url)
                                    if p_analysis.get('success'):
                                        recov_cars = p_analysis.get('recoverable_cars', [])
                                        # Nếu xe này không còn trong danh sách xe lỗi
                                        if clean_plate(biensoxe) not in [clean_plate(c) for c in recov_cars]:
                                            print(f"🎉 [AutoSync] Xe {biensoxe} của học viên {mahv} đã HẾT PHIÊN LỖI!")
                                            with self.lock:
                                                if tid in self.queue:
                                                    self.queue[tid]['status'] = 'completed'
                                                    self.queue[tid]['log'] = "✅ Đã hết phiên lỗi trên xe này! Tổng cục đã tiếp nhận."
                                                    self._save_queue()
                    except Exception as eq:
                        print(f"⚠️ [AutoSync] Lỗi kiểm tra lại phiên: {eq}")

            except Exception as e:
                print(f"⚠️ [AutoSync] Lỗi trong worker loop: {e}")

            time.sleep(15)  # Chu kỳ kiểm tra 15 giây

# Global instance
_sync_manager_instance = None

def get_sync_manager(ds_getter=None, my_student_checker=None):
    global _sync_manager_instance
    if _sync_manager_instance is None:
        _sync_manager_instance = AutoSyncManager(discord_service_getter=ds_getter, my_student_checker=my_student_checker)
        _sync_manager_instance.start_worker()
    elif my_student_checker and not _sync_manager_instance.is_my_student:
        _sync_manager_instance.is_my_student = my_student_checker
    return _sync_manager_instance
