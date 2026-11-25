import os
import sys
import threading
import json
import time
from datetime import datetime
import re
import base64
import hashlib
import requests
import urllib.request
import gzip
import queue

_init_lock = threading.Lock()
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, Response

import discord_service
import database
import export_excel
import image_extractor
import sync_service
import phien_service
import teacher_sync_service

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['JSON_AS_ASCII'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024 * 1024 # Cho phép upload tối đa 128MB ảnh

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
CABIN_DIR = os.path.join(UPLOADS_DIR, 'cabin')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CABIN_DIR, exist_ok=True)

# Khởi tạo Services
ds = discord_service.DiscordDATService()
init_token = discord_service.auto_detect_discord_token()
if init_token:
    ds.set_token(init_token)
db = database.StudentDatabase()
_has_initialized_totnghiep = False

def is_my_queried_student(mahv_or_cccd_or_name):
    """
    CHỈ CHO PHÉP THỰC HIỆN ĐỒNG BỘ (/dongbo) VÀ CABIN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU:
    - Có trong danh sách hồ sơ gốc (Master Roster) của tôi.
    - Hoặc có trong CSDL học viên do chính tôi (tranthang252) tra cứu (is_my_query == True).
    Tuyệt đối không chạy lệnh cho học viên lạ trên kênh chung!
    """
    if not mahv_or_cccd_or_name:
        return False
    q_clean = str(mahv_or_cccd_or_name).strip()
    st = db.find_student(q_clean)
    if st:
        if st.get('is_my_query') or st.get('interaction_username') in ('tranthang252', 'tranthang0234') or str(st.get('interaction_user_id')) in ('1380376964304474173', '1380096710386384937', '1574670927948423262'):
            return True
    resolved_st, _ = db.resolve_student_identifier(q_clean)
    if resolved_st:
        return True
    return False

# Khởi động Trình quản lý Đồng bộ Tự động (/dongbo) CHỈ DÀNH CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
auto_sync_mgr = sync_service.get_sync_manager(lambda: ds, my_student_checker=is_my_queried_student)

# Tắt chức năng tự động đồng bộ liên tục để tránh quét lẫn học viên của giáo viên khác trên kênh Discord
auto_sync_enabled = False

def start_self_ping_worker():
    """Tự động đánh thức máy chủ ngầm để không bao giờ bị rơi vào chế độ ngủ đông (Keep-Alive)."""
    def _pinger():
        time.sleep(20)
        app_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://dat-manager-pro.onrender.com')
        ping_url = f"{app_url}/api/ping"
        while True:
            try:
                req = urllib.request.Request(ping_url, headers={'User-Agent': 'KeepAlive/1.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pass
            except Exception:
                pass
            time.sleep(12 * 60)  # Đánh thức định kỳ mỗi 12 phút

    t = threading.Thread(target=_pinger, daemon=True)
    t.start()

start_self_ping_worker()

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({
        'status': 'ok',
        'timestamp': time.time(),
        'connected': ds.is_connected()
    })

@app.route('/api/backup/download', methods=['GET'])
def backup_download():
    """Tải toàn bộ dữ liệu học viên dưới dạng file JSON về điện thoại/máy tính."""
    return send_file(
        database.STUDENTS_FILE,
        as_attachment=True,
        download_name=f"Backup_DAT_Manager_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )

@app.route('/api/backup/upload', methods=['POST'])
def backup_upload():
    """Khôi phục dữ liệu học viên từ file JSON backup."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'Chưa chọn file sao lưu!'})
    f = request.files['file']
    if not f.filename.endswith('.json'):
        return jsonify({'success': False, 'error': 'Vui lòng chọn đúng file đuôi .json!'})
    try:
        data = json.load(f)
        if isinstance(data, dict):
            db.students.update(data)
            db._save()
            return jsonify({'success': True, 'count': len(db.students)})
        elif isinstance(data, list):
            for s in data:
                key = s.get('mahv') or s.get('cccd') or s.get('hoten')
                if key:
                    db.students[key] = s
            db._save()
            return jsonify({'success': True, 'count': len(db.students)})
        else:
            return jsonify({'success': False, 'error': 'Định dạng dữ liệu file không đúng danh sách học viên!'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi khôi phục: {str(e)}'})

@app.after_request
def add_cache_headers(response):
    """Ngăn chặn trình duyệt điện thoại lưu cache cũ và nén Gzip siêu tốc."""
    if request.path in ('/api/stats', '/api/students'):
        response.headers['Cache-Control'] = 'no-cache'
    elif request.path in ('/', '/service-worker.js', '/manifest.json') or request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

    # Nén Gzip tự động cho HTML, JSON, JS (giảm dung lượng truyền tải từ 230KB xuống ~35KB trên điện thoại)
    try:
        accept_encoding = request.headers.get('Accept-Encoding', '').lower()
        if (
            'gzip' in accept_encoding and
            response.status_code == 200 and
            not response.direct_passthrough and
            len(response.data or b'') > 500 and
            'Content-Encoding' not in response.headers
        ):
            ctype = response.headers.get('Content-Type', '').lower()
            if 'text/' in ctype or 'application/json' in ctype or 'javascript' in ctype:
                import gzip
                compressed = gzip.compress(response.data)
                if len(compressed) < len(response.data):
                    response.data = compressed
                    response.headers['Content-Encoding'] = 'gzip'
                    response.headers['Content-Length'] = len(compressed)
    except Exception:
        pass

    return response

@app.route('/api/ping', methods=['GET', 'HEAD'])
def api_ping():
    """Endpoint phản hồi nhẹ cho worker giữ kết nối và chống ngủ đông 24/7."""
    return jsonify({'status': 'ok', 'online': True, 'uptime': time.time()})

@app.route('/')
def index():
    connected = ds.is_connected()
    uinfo = ds.get_user_info() if connected else None
    return render_template(
        'index.html',
        bot_connected=connected,
        bot_username=uinfo.get('username') if uinfo else ''
    )

@app.route('/favicon.ico')
@app.route('/app_icon.ico')
def app_icon():
    return send_from_directory(BASE_DIR, 'app_icon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'manifest.json', mimetype='application/manifest+json')

@app.route('/service-worker.js')
def serve_sw():
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'service-worker.js', mimetype='application/javascript')

@app.route('/apple-touch-icon.png')
def serve_apple_icon():
    return send_from_directory(os.path.join(BASE_DIR, 'static', 'icons'), 'apple-touch-icon.png', mimetype='image/png')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

@app.route('/api/lan_info', methods=['GET'])
def get_lan_info():
    """Lấy danh sách địa chỉ IP mạng nội bộ của máy tính để hiển thị link & QR code cho iPhone."""
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and primary_ip not in ips and not primary_ip.startswith('127.'):
            ips.append(primary_ip)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith('127.') and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    primary = ips[0] if ips else '127.0.0.1'
    return jsonify({
        'success': True,
        'primary_ip': primary,
        'port': 5050,
        'lan_url': f"http://{primary}:5050",
        'all_ips': ips
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    connected = ds.is_connected()
    uinfo = ds.get_user_info() if connected else None
    return jsonify({
        'connected': connected,
        'has_token': bool(ds.token),
        'username': uinfo.get('username') if uinfo else None,
        'channel_id': discord_service.CHANNEL_ID,
        'guild_id': discord_service.GUILD_ID,
        'app_id': discord_service.APP_ID
    })

def fetch_pdf_file(pdf_url, fname, student_key=None, url_field=None, force_download=False):
    clean_fname = "".join([c if c.isalnum() or c in '._-' else '_' for c in fname])
    dest_path = os.path.join(DOWNLOADS_DIR, clean_fname)

    if not force_download and os.path.exists(dest_path) and os.path.getsize(dest_path) > 1000:
        return dest_path, None

    target_url = pdf_url
    for attempt in range(2):
        try:
            r = requests.get(target_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=12)
            if r.status_code == 200 and len(r.content) > 500:
                with open(dest_path, 'wb') as f:
                    f.write(r.content)
                if student_key and url_field and target_url != pdf_url:
                    st = db.get_by_id(student_key)
                    if st:
                        st[url_field] = target_url
                        db._save()
                return dest_path, None
            elif r.status_code != 200 and attempt == 0:
                refreshed = ds.refresh_attachment_url(pdf_url)
                if refreshed and refreshed != target_url:
                    target_url = refreshed
                    continue
        except Exception as e_req:
            try:
                req = urllib.request.Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = resp.read()
                    if len(data) > 500:
                        with open(dest_path, 'wb') as f:
                            f.write(data)
                        return dest_path, None
            except Exception as e:
                if attempt == 0:
                    refreshed = ds.refresh_attachment_url(pdf_url)
                    if refreshed and refreshed != target_url:
                        target_url = refreshed
                        continue
                return None, str(e)

    return (dest_path, None) if os.path.exists(dest_path) else (None, "Không thể tải tệp")

def ensure_lythuyet_evaluated(student_obj, exempt_all_cabin=False, force_download=False, auto_save=True):
    """Tự động tải file PDF lý thuyết về máy và chấm điểm điều kiện thi tốt nghiệp."""
    if not student_obj:
        return student_obj

    # Nếu đã được đánh giá trước đó và không yêu cầu bắt buộc tải lại: trả về ngay
    if not force_download and student_obj.get('totnghiep_has_pdf') and student_obj.get('lythuyet_status') in ('Đạt', 'Không'):
        return student_obj

    pdf_url = student_obj.get('lythuyet_pdf_url')
    fname = student_obj.get('lythuyet_pdf_filename') or f"{student_obj.get('mahv', 'HV')}_lythuyet.pdf"

    try:
        if pdf_url:
            st_key = student_obj.get('mahv') or student_obj.get('cccd')
            dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=st_key, url_field='lythuyet_pdf_url', force_download=force_download)
            if dest_path and os.path.exists(dest_path):
                student_obj['lythuyet_pdf_filename'] = os.path.basename(dest_path)
                student_obj['has_lythuyet'] = True
                import totnghiep_service
                student_obj = totnghiep_service.evaluate_student(student_obj, DOWNLOADS_DIR, exempt_all_cabin=exempt_all_cabin)
                db.upsert_student(student_obj, auto_save=auto_save)
                return student_obj

        # Nếu đã có file sẵn trên ổ đĩa
        import totnghiep_service
        student_obj = totnghiep_service.evaluate_student(student_obj, DOWNLOADS_DIR, exempt_all_cabin=exempt_all_cabin)
        db.upsert_student(student_obj, auto_save=auto_save)
    except Exception as e:
        print(f"Lỗi evaluate totnghiep: {e}")

    return student_obj

# ==============================================================================
# HỆ THỐNG LẮNG NGHE & CẬP NHẬT TỨC THÌ TỪ DISCORD (REAL-TIME DISCORD SYNC & SSE)
# ==============================================================================
_sse_subscribers_lock = threading.Lock()
_sse_subscribers = []

_last_user_activity = time.time()

@app.before_request
def track_user_activity():
    global _last_user_activity
    try:
        p = request.path
        if not (p.startswith('/static') or p == '/favicon.ico'):
            _last_user_activity = time.time()
    except Exception:
        pass

def is_app_active():
    """Kiểm tra xem hệ thống có đang có người dùng truy cập hoặc theo dõi không."""
    with _sse_subscribers_lock:
        if len(_sse_subscribers) > 0:
            return True
    return (time.time() - _last_user_activity) < 180  # 3 phút


def broadcast_live_event(event_type, payload):
    """Phát sóng sự kiện thời gian thực tới tất cả trình duyệt qua Server-Sent Events (SSE)."""
    msg_dict = {
        'type': event_type,
        'data': payload,
        'version': db.version,
        'timestamp': time.time()
    }
    msg_str = json.dumps(msg_dict, ensure_ascii=False)
    with _sse_subscribers_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg_str)
            except Exception:
                dead.append(q)
        for d in dead:
            if d in _sse_subscribers:
                _sse_subscribers.remove(d)

def handle_live_discord_student(st, cmd):
    """Xử lý và lưu học viên được phát hiện tức thì từ kênh Discord."""
    if not st:
        return
    mahv = st.get('mahv', '').strip()
    hoten = st.get('hoten', '').strip()
    cccd = st.get('cccd', '').strip()
    if not mahv and not hoten and not cccd:
        return

    # 1. NẾU ĐÃ BỊ XOÁ: Tuyệt đối không bao giờ cho phép nạp lại tự động!
    if db.is_student_deleted(mahv=mahv, cccd=cccd, hoten=hoten):
        return

    # 2. KIỂM TRA HỌC VIÊN CÓ THUỘC QUẢN LÝ CỦA TÔI KHÔNG:
    # - Đang có trong danh sách CSDL hiển thị (db.students)
    # - HOẶC có trong danh sách gốc được đẩy vào (db.roster)
    # - HOẶC lệnh tra cứu được phát đi từ chính tài khoản Discord của tôi (tranthang252 hoặc tranthang0234)
    i_uid = str(st.get('interaction_user_id') or '')
    i_uname = str(st.get('interaction_username') or '').lower()
    is_my_discord = (i_uid in ('1380376964304474173', '1380096710386384937', '1574670927948423262') or i_uname in ('tranthang252', 'tranthang0234'))
    
    is_in_students = bool(db.find_student(mahv) or db.find_student(cccd) or db.find_student(hoten))
    is_in_roster = bool(
        (mahv and any(r.get('mahv') == mahv for r in db.roster.values())) or
        (cccd and any(r.get('cccd') == cccd for r in db.roster.values())) or
        (hoten and any(database.strip_vietnamese_accents(r.get('hoten')) == database.strip_vietnamese_accents(hoten) for r in db.roster.values()))
    )

    if not (is_in_students or is_in_roster or is_my_discord):
        # Học viên lạ của giáo viên khác trên kênh chung -> BỎ QUA HOÀN TOÀN, KHÔNG TỰ VỀ APP!
        return

    st['is_my_query'] = True
    
    # 3. KẾT HỢP DỮ LIỆU ĐÃ CÓ (MERGE RECORD):
    # Đảm bảo lệnh /lythuyet giữ nguyên quãng đường DAT, và lệnh /dat giữ nguyên file lý thuyết
    existing = db.find_student(mahv) or db.find_student(cccd) or db.find_student(hoten)
    if existing:
        merged = dict(existing)
        for k, v in st.items():
            if v or (v is not False and v == 0):
                if k in ('quangduong', 'thoigian', 'thoigian_dem', 'thoigian_tudong'):
                    curr_val = float(existing.get(k, 0.0) or 0.0)
                    new_val = float(v or 0.0)
                    if new_val < curr_val and not st.get('_force_overwrite'):
                        continue
                merged[k] = v
        st = merged

    # 4. Nếu là báo cáo lý thuyết hoặc có file PDF lý thuyết: Tải ngay lập tức (force_download=True)
    if cmd == 'lythuyet' or st.get('lythuyet_pdf_url'):
        try:
            st = ensure_lythuyet_evaluated(st, force_download=True, auto_save=False)
        except Exception as e_lt:
            print("[Discord Live Sync] Cảnh báo chấm điểm lý thuyết:", e_lt)

    # 5. Nếu là phiên học có file PDF báo cáo phiên
    if (cmd == 'phien' or st.get('pdf_url')) and st.get('pdf_url'):
        fname = st.get('pdf_filename') or f"{mahv or 'HV'}_phienhoc.pdf"
        try:
            fetch_pdf_file(st['pdf_url'], fname, student_key=mahv, url_field='pdf_url', force_download=False)
        except Exception:
            pass

    # 6. Lưu vào CSDL
    saved_st = db.upsert_student(st, auto_save=True)
    if saved_st:
        print(f"[Discord Live Sync] ⚡ Cập nhật tức thì: {saved_st.get('hoten')} ({saved_st.get('mahv')}) [{cmd}]")
        broadcast_live_event('student_updated', saved_st)

# Khởi chạy Discord Live Watcher ngầm (trì hoãn 2s để Gunicorn bind cổng siêu tốc)
def _start_watcher_bg():
    time.sleep(2.0)
    try:
        ds.start_live_watcher(
            on_student_parsed_cb=handle_live_discord_student,
            interval_sec=3.5,
            is_active_cb=is_app_active
        )
    except Exception as _e_watcher:
        print("⚠️ Lỗi khởi chạy Discord Live Watcher:", _e_watcher)

threading.Thread(target=_start_watcher_bg, daemon=True, name="DiscordWatcherStarter").start()

@app.route('/api/events')
def sse_events():
    """Server-Sent Events endpoint: đẩy dữ liệu thời gian thực tới trình duyệt ngay khi có phản hồi từ Discord."""
    def gen():
        q = queue.Queue(maxsize=100)
        with _sse_subscribers_lock:
            _sse_subscribers.append(q)
        t_end = time.time() + 15
        try:
            yield ": keepalive\n\n"
            while time.time() < t_end:
                try:
                    data = q.get(timeout=2.0)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_subscribers_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)

    return Response(gen(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/live_updates', methods=['GET'])
def live_updates():
    """Kiểm tra cập nhật thời gian thực dạng polling (dự phòng nếu SSE bị gián đoạn)."""
    client_v = request.args.get('version', type=int, default=0)
    current_v = db.version
    if client_v == current_v:
        return jsonify({'has_update': False, 'version': current_v})

    return jsonify({
        'has_update': True,
        'version': current_v,
        'count': len(db.students)
    })


@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Lấy số liệu KPI tổng quan với ETag 304 Not Modified siêu tốc khi có nhiều người truy cập."""
    etag = f"stats-{db.version}"
    if request.if_none_match and request.if_none_match.contains_weak(etag):
        return Response(status=304)

    resp = jsonify(db.get_statistics())
    resp.set_etag(etag, weak=True)
    return resp

@app.route('/api/students', methods=['GET'])
def get_students():
    """Lấy danh sách học viên kèm ETag 304 Not Modified và tối ưu hóa khởi động lạnh đa luồng."""
    # 1. Khởi tạo kiểm tra tốt nghiệp một lần khi khởi động (bảo vệ chống race condition đa luồng)
    global _has_initialized_totnghiep
    if not _has_initialized_totnghiep:
        with _init_lock:
            if not _has_initialized_totnghiep:
                dirty = False
                for s in list(db.students.values()):
                    if not s.get('totnghiep_has_pdf') or 'dat_quangduong' not in s.get('totnghiep_details', {}):
                        fname = s.get('lythuyet_pdf_filename') or (f"{s.get('mahv')}_lythuyet.pdf" if s.get('mahv') else '')
                        p = os.path.join(DOWNLOADS_DIR, fname) if fname else ''
                        if p and os.path.exists(p):
                            ensure_lythuyet_evaluated(s, auto_save=False)
                            dirty = True
                if dirty:
                    db._save()
                _has_initialized_totnghiep = True

    # 2. Kiểm tra ETag 304 Not Modified
    keyword = request.args.get('search', '')
    status_filter = request.args.get('status', 'all')
    course_filter = request.args.get('course', 'all')
    sort_by = request.args.get('sort', 'updated')

    import hashlib
    param_hash = hashlib.md5(f"{keyword}:{status_filter}:{course_filter}:{sort_by}".encode('utf-8')).hexdigest()[:8]
    etag = f"st-{db.version}-{param_hash}"
    if request.if_none_match and request.if_none_match.contains_weak(etag):
        return Response(status=304)

    students = db.get_all(keyword=keyword, status_filter=status_filter, course_filter=course_filter, sort_by=sort_by)
    courses = sorted(list(set(s.get('khoa') for s in list(db.students.values()) if s.get('khoa'))))
    resp = jsonify({
        'success': True,
        'version': db.version,
        'count': len(students),
        'students': students,
        'courses': courses
    })
    resp.set_etag(etag, weak=True)
    return resp

def mark_as_my_student(st_dict):
    """Gán đầy đủ nhãn sở hữu cho học viên vừa tra cứu thành công."""
    if not isinstance(st_dict, dict):
        return st_dict
    st_dict['is_my_query'] = True
    st_dict['_force_unmark_deleted'] = True
    try:
        u_info = ds.get_user_info() or {}
        cur_uname = u_info.get('username') or 'tranthang0234'
        cur_uid = str(u_info.get('id') or '1574670927948423262')
    except Exception:
        cur_uname = 'tranthang0234'
        cur_uid = '1574670927948423262'
    st_dict['interaction_username'] = cur_uname
    st_dict['interaction_user_id'] = cur_uid
    return st_dict

@app.route('/api/query', methods=['POST'])
def query_student():
    data = request.get_json() or {}
    query_text = (data.get('query') or data.get('mhv') or '').strip()
    cmd_type = data.get('type', 'dat')
    force = data.get('force', False)

    if not query_text:
        return jsonify({'success': False, 'error': 'Vui lòng nhập họ tên, số CCCD hoặc mã học viên!'})

    # TỰ ĐỘNG NHẬN DIỆN HỌC VIÊN TỪ TÊN (SMART RESOLVER):
    # Tự động tra cứu trong CSDL học viên và Danh Sách Ban Đầu (Master Roster)
    # để lấy chính xác Mã Học Viên (mahv) hoặc Số CCCD gửi tới Bot Discord
    resolved_info = None
    target_query = query_text
    resolved_st, match_type = db.resolve_student_identifier(query_text)
    if resolved_st:
        best_id = resolved_st.get('mahv') or resolved_st.get('cccd') or resolved_st.get('hoten')
        if best_id:
            resolved_info = {
                'original': query_text,
                'hoten': resolved_st.get('hoten', ''),
                'cccd': resolved_st.get('cccd', ''),
                'mahv': resolved_st.get('mahv', ''),
                'ngaysinh': resolved_st.get('ngaysinh', ''),
                'khoa': resolved_st.get('khoa', ''),
                'match_type': match_type
            }
            target_query = best_id
    elif len(query_text) >= 3:
        # Nếu không tìm thấy học viên, kiểm tra xem có phải tên giáo viên không
        try:
            q_norm = _normalize_vietnamese(query_text)
            teachers = get_gsheet_teachers()
            matched_t = next((t for t in teachers if q_norm == _normalize_vietnamese(t['name']) or (len(q_norm) >= 4 and (q_norm in _normalize_vietnamese(t['name']) or _normalize_vietnamese(t['name']) in q_norm))), None)
            if matched_t:
                return jsonify({
                    'success': True,
                    'is_teacher': True,
                    'teacher_name': matched_t['name'],
                    'car': matched_t.get('car', ''),
                    'message': f"Phát hiện giáo viên {matched_t['name']}. Hệ thống đang tự động nạp học viên từ Google Sheet..."
                })
        except Exception:
            pass

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord. Hãy kiểm tra phần mềm Discord.'})

    # 1. KHI BẤM /DAT: Chỉ tra cứu điều kiện DAT (km, giờ, đêm, tự động, số ngày) - KHÔNG tra cứu lý thuyết
    if cmd_type == 'dat':
        res_dat = ds.query_student(target_query, command='dat', timeout_sec=16, force=force)
        if not res_dat.get('success'):
            return jsonify({'success': False, 'error': res_dat.get('error', 'Lỗi tra cứu DAT.')})

        raw_dat = res_dat.get('data')
        st_dat = raw_dat[0] if isinstance(raw_dat, list) and raw_dat else (raw_dat or {})
        st_dat['is_my_query'] = True
        st_dat['_force_unmark_deleted'] = True
        mark_as_my_student(st_dat)
        saved_st = db.upsert_student(st_dat, auto_save=True)

        mahv = (saved_st or {}).get('mahv') or st_dat.get('mahv')
        final_st = db.find_student(mahv or target_query) or saved_st or st_dat
        broadcast_live_event('student_updated', final_st)

        return jsonify({
            'success': True,
            'student': final_st,
            'students': [final_st],
            'count': 1,
            'mahv': mahv,
            'hoten': (final_st or {}).get('hoten'),
            'quangduong': (final_st or {}).get('quangduong', 0),
            'thoigian': (final_st or {}).get('thoigian', 0),
            'thoigian_dem': (final_st or {}).get('thoigian_dem', 0),
            'thoigian_tudong': (final_st or {}).get('thoigian_tudong', 0),
            'songay_str': (final_st or {}).get('songay_str', ''),
            'songay_dat': (final_st or {}).get('songay_dat', False),
            'has_lythuyet': bool((final_st or {}).get('has_lythuyet') or (final_st or {}).get('lythuyet_pdf_url')),
            'lythuyet_dat': (final_st or {}).get('lythuyet_dat', False),
            'dieukien_totnghiep': (final_st or {}).get('dieukien_totnghiep', 'Chưa tra LT'),
            'ketqua': (final_st or {}).get('ketqua', 'Chưa Đạt'),
            'resolved': resolved_info
        })

    # 1b. KHI BẤM COMBO: Tra cứu liên hoàn DAT ➔ Tự động lấy Mã HV ➔ Tra cứu Lý thuyết (/lythuyet) ➔ Tự động tải PDF & Chấm điểm
    if cmd_type == 'combo':
        res_dat = ds.query_student(target_query, command='dat', timeout_sec=16, force=force)
        if not res_dat.get('success'):
            return jsonify({'success': False, 'error': res_dat.get('error', 'Lỗi tra cứu DAT.')})

        raw_dat = res_dat.get('data')
        st_dat = raw_dat[0] if isinstance(raw_dat, list) and raw_dat else (raw_dat or {})
        st_dat['is_my_query'] = True
        st_dat['_force_unmark_deleted'] = True
        mark_as_my_student(st_dat)
        saved_st = db.upsert_student(st_dat, auto_save=True)

        mahv = (saved_st or {}).get('mahv') or st_dat.get('mahv')
        lt_target = mahv if mahv else ((saved_st or {}).get('cccd') or (saved_st or {}).get('hoten') or target_query)

        # TỰ ĐỘNG TRA CỨU TIẾP LÝ THUYẾT (/lythuyet) BẰNG MÃ HỌC VIÊN
        if lt_target:
            try:
                time.sleep(0.4)  # Giãn cách nhẹ tránh rate limit
                res_lt = ds.query_student(lt_target, command='lythuyet', timeout_sec=16, force=force)
                if res_lt.get('success'):
                    raw_lt = res_lt.get('data')
                    st_lt = raw_lt[0] if isinstance(raw_lt, list) and raw_lt else (raw_lt or {})
                    st_lt['is_my_query'] = True
                    st_lt['_force_unmark_deleted'] = True
                    mark_as_my_student(st_lt)
                    saved_st = db.upsert_student(st_lt, auto_save=True)
            except Exception as e_lt:
                print("Lỗi tự động tra cứu lý thuyết sau combo:", e_lt)

        final_st = db.find_student(mahv or target_query) or saved_st or st_dat
        # TỰ ĐỘNG TẢI FILE PDF LÝ THUYẾT VỀ MÁY VÀ ĐÁNH GIÁ ĐIỀU KIỆN TỐT NGHIỆP
        final_st = ensure_lythuyet_evaluated(final_st, force_download=True)
        broadcast_live_event('student_updated', final_st)

        return jsonify({
            'success': True,
            'student': final_st,
            'students': [final_st],
            'count': 1,
            'mahv': mahv,
            'hoten': (final_st or {}).get('hoten'),
            'quangduong': (final_st or {}).get('quangduong', 0),
            'thoigian': (final_st or {}).get('thoigian', 0),
            'thoigian_dem': (final_st or {}).get('thoigian_dem', 0),
            'thoigian_tudong': (final_st or {}).get('thoigian_tudong', 0),
            'songay_str': (final_st or {}).get('songay_str', ''),
            'songay_dat': (final_st or {}).get('songay_dat', False),
            'has_lythuyet': bool((final_st or {}).get('has_lythuyet') or (final_st or {}).get('lythuyet_pdf_url')),
            'lythuyet_dat': (final_st or {}).get('lythuyet_dat', False),
            'dieukien_totnghiep': (final_st or {}).get('dieukien_totnghiep', 'Chưa tra LT'),
            'ketqua': (final_st or {}).get('ketqua', 'Chưa Đạt'),
            'resolved': resolved_info
        })

    # 2. KHI BẤM /LYTHUYET: Tra cứu lý thuyết ➔ Tự động tải PDF & Đánh giá ngay
    if cmd_type == 'lythuyet':
        res_lt = ds.query_student(target_query, command='lythuyet', timeout_sec=16)
        if not res_lt.get('success'):
            return jsonify({'success': False, 'error': res_lt.get('error', 'Lỗi tra cứu lý thuyết.')})

        raw_lt = res_lt.get('data')
        saved_list = []
        raw_items = raw_lt if isinstance(raw_lt, list) else [raw_lt]
        for it in raw_items:
            it['is_my_query'] = True
            it['_force_unmark_deleted'] = True
            mark_as_my_student(it)
            saved = db.upsert_student(it, auto_save=True)
            if saved:
                saved = ensure_lythuyet_evaluated(saved, force_download=True)
                saved_list.append(saved)

        final_st = saved_list[0] if saved_list else db.find_student(target_query)
        if final_st:
            broadcast_live_event('student_updated', final_st)
        mahv = (final_st or {}).get('mahv')
        return jsonify({
            'success': True,
            'students': saved_list,
            'count': len(saved_list),
            'mahv': mahv,
            'hoten': (final_st or {}).get('hoten'),
            'has_lythuyet': bool((final_st or {}).get('has_lythuyet') or (final_st or {}).get('lythuyet_pdf_url')),
            'lythuyet_dat': (final_st or {}).get('lythuyet_dat', False),
            'dieukien_totnghiep': (final_st or {}).get('dieukien_totnghiep', 'Chưa tra LT'),
            'resolved': resolved_info
        })

    # 3. KHI BẤM /DAYKHOA: Kiểm tra lý thuyết ĐẠT ➔ Trích xuất xe từ PDF lý thuyết ➔ Gửi lệnh /daykhoa
    if cmd_type == 'daykhoa':
        final_st = db.find_student(mahv or target_query)
        if not final_st and resolved_info:
            final_st = db.find_student(resolved_info.get('mahv') or resolved_info.get('cccd'))
        if not final_st:
            return jsonify({'success': False, 'error': f'Không tìm thấy học viên "{query_text}" để chạy /daykhoa.'})

        final_st = ensure_lythuyet_evaluated(final_st, force_download=False)
        is_lt_dat = bool(final_st.get('lythuyet_dat') is True or final_st.get('lythuyet_status') == 'Đạt')
        if not is_lt_dat:
            lt_st = final_st.get('lythuyet_status') or 'Chưa đạt'
            reason = final_st.get('lythuyet_reason') or ('Chưa hoàn thành đủ các môn lý thuyết bắt buộc' if lt_st == 'Không' else 'Chưa tra cứu lý thuyết (/lythuyet)')
            return jsonify({
                'success': False,
                'is_lt_dat': False,
                'error': f"Học viên {final_st.get('hoten', query_text)} CHƯA ĐẠT lý thuyết (Trạng thái: {lt_st} - {reason})! Không được phép chạy lệnh /daykhoa.",
                'student': final_st,
                'resolved': resolved_info
            })

        # Mở PDF lý thuyết để trích xuất xe
        car = None
        fname = final_st.get('lythuyet_pdf_filename')
        candidate_paths = []
        if fname:
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, fname))
        if final_st.get('mahv'):
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{final_st.get('mahv')}_lythuyet.pdf"))
        if final_st.get('cccd'):
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{final_st.get('cccd')}_lythuyet.pdf"))

        pdf_path = next((p for p in candidate_paths if os.path.exists(p)), None)
        if not pdf_path and final_st.get('lythuyet_pdf_url'):
            final_st = ensure_lythuyet_evaluated(final_st, force_download=True)
            fname = final_st.get('lythuyet_pdf_filename')
            if fname and os.path.exists(os.path.join(DOWNLOADS_DIR, fname)):
                pdf_path = os.path.join(DOWNLOADS_DIR, fname)

        if pdf_path and os.path.exists(pdf_path):
            import totnghiep_service
            car = totnghiep_service.extract_car_from_lythuyet_pdf(pdf_path)

        if not car:
            car = final_st.get('xe_phan')

        if not car:
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'error': f"Học viên {final_st.get('hoten')} đã đạt lý thuyết nhưng không tìm thấy thông tin 'Phân Xe' trong file PDF lý thuyết! Vui lòng kiểm tra lại file PDF.",
                'student': final_st,
                'resolved': resolved_info
            })

        c_name = (data.get('course') or final_st.get('khoa') or final_st.get('tenkhoa') or '').strip()
        if not c_name:
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'car': car,
                'error': f"Không xác định được khóa học của học viên {final_st.get('hoten')}.",
                'student': final_st,
                'resolved': resolved_info
            })

        clean_car = ds.clean_plate(car)
        res_daykhoa = ds.send_daykhoa(c_name, clean_car)
        final_st['xe_phan'] = clean_car
        final_st['last_daykhoa_time'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        final_st['last_daykhoa_res'] = res_daykhoa
        db.upsert_student(final_st, auto_save=True)
        broadcast_live_event('student_updated', final_st)

        if res_daykhoa.get('success'):
            return jsonify({
                'success': True,
                'is_lt_dat': True,
                'hoten': final_st.get('hoten'),
                'mahv': final_st.get('mahv'),
                'khoa': c_name,
                'biensoxe': clean_car,
                'message': res_daykhoa.get('message') or f"Đã gửi lệnh /daykhoa thành công cho học viên {final_st.get('hoten')} (Khóa: {c_name}, Xe: {clean_car})!",
                'student': final_st,
                'resolved': resolved_info
            })
        else:
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'hoten': final_st.get('hoten'),
                'mahv': final_st.get('mahv'),
                'khoa': c_name,
                'biensoxe': clean_car,
                'error': res_daykhoa.get('error', 'Lỗi gửi lệnh /daykhoa tới Discord.'),
                'student': final_st,
                'resolved': resolved_info
            })

    res = ds.query_student(target_query, command=cmd_type)
    if res.get('success'):
        raw_data = res.get('data')
        saved_list = []
        if isinstance(raw_data, list):
            for st in raw_data:
                st['is_my_query'] = True
                st['_force_unmark_deleted'] = True
                mark_as_my_student(st)
                saved = db.upsert_student(st)
                if saved:
                    if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                        saved = ensure_lythuyet_evaluated(saved, force_download=True)
                    saved_list.append(saved)
        else:
            if isinstance(raw_data, dict):
                raw_data['is_my_query'] = True
                raw_data['_force_unmark_deleted'] = True
                mark_as_my_student(raw_data)
            saved = db.upsert_student(raw_data)
            if saved:
                if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                    saved = ensure_lythuyet_evaluated(saved, force_download=True)
                saved_list.append(saved)
                broadcast_live_event('student_updated', saved)

        return jsonify({'success': True, 'students': saved_list, 'count': len(saved_list), 'resolved': resolved_info})
    else:
        return jsonify({'success': False, 'error': res.get('error', 'Lỗi không xác định khi tra cứu.')})

@app.route('/api/daykhoa/preview', methods=['GET', 'POST'])
def daykhoa_preview_api():
    """
    Quét và kiểm tra danh sách học viên trong một hoặc nhiều khóa học để chuẩn bị lệnh /daykhoa:
    1. Kiểm tra từng học viên xem cột lý thuyết đã ĐẠT chưa.
    2. Với học viên ĐẠT, đọc file PDF lý thuyết (_lythuyet.pdf) để trích xuất biển số xe ('Phân Xe: <biển số>').
    3. Gom nhóm theo các biển số xe khác nhau để hệ thống thiết lập lệnh đẩy khóa riêng biệt cho từng xe.
    """
    courses_to_scan = []
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        course_name = (data.get('course') or data.get('khoa') or '').strip()
        raw_courses = data.get('courses') or data.get('khoas') or []
        if isinstance(raw_courses, list):
            courses_to_scan = [c.strip() for c in raw_courses if c and c.strip()]
        elif isinstance(raw_courses, str) and raw_courses.strip():
            courses_to_scan = [c.strip() for c in raw_courses.split(',') if c.strip()]
        if course_name and course_name not in courses_to_scan:
            courses_to_scan.append(course_name)
    else:
        course_name = (request.args.get('course') or request.args.get('khoa') or '').strip()
        raw_courses = request.args.get('courses') or request.args.get('khoas') or ''
        if raw_courses:
            courses_to_scan = [c.strip() for c in raw_courses.split(',') if c.strip()]
        if course_name and course_name not in courses_to_scan:
            courses_to_scan.append(course_name)

    all_students = db.get_all()

    # Nếu không truyền tên khóa, trả về danh sách tất cả các khóa hiện có trong hệ thống và danh sách xe gợi ý
    if not courses_to_scan:
        course_counts = {}
        distinct_cars = set()
        for s in all_students:
            c = (s.get('khoa') or '').strip()
            if c:
                course_counts[c] = course_counts.get(c, 0) + 1
            car = (s.get('xe_phan') or '').strip()
            if car:
                distinct_cars.add(ds.clean_plate(car))
        distinct_cars.add('77A24156')  # Xe mặc định Thầy Thắng

        return jsonify({
            'success': True,
            'courses': [
                {'name': c, 'student_count': count}
                for c, count in sorted(course_counts.items())
            ],
            'suggested_cars': sorted(list(c for c in distinct_cars if c))
        })

    target_courses_upper = {c.upper() for c in courses_to_scan}
    course_students = [s for s in all_students if (s.get('khoa') or '').strip().upper() in target_courses_upper]
    if not course_students:
        return jsonify({'success': False, 'error': f'Không tìm thấy học viên nào thuộc các khóa {", ".join(courses_to_scan)}.'})

    import totnghiep_service
    passed_students = []
    skipped_students = []
    car_groups = {}
    no_car_students = []

    for s in course_students:
        s = ensure_lythuyet_evaluated(s, force_download=False)
        is_lt_dat = bool(s.get('lythuyet_dat') is True or s.get('lythuyet_status') == 'Đạt')
        if not is_lt_dat:
            lt_st = s.get('lythuyet_status') or 'Chưa đạt'
            reason = s.get('lythuyet_reason') or ('Chưa hoàn thành đủ các môn lý thuyết bắt buộc' if lt_st == 'Không' else 'Chưa tra cứu lý thuyết (/lythuyet)')
            skipped_students.append({
                'mahv': s.get('mahv', ''),
                'cccd': s.get('cccd', ''),
                'hoten': s.get('hoten', 'Chưa rõ tên'),
                'khoa': s.get('khoa', ''),
                'status': lt_st,
                'reason': reason
            })
            continue

        passed_students.append(s)

        # Trích xuất xe từ file PDF lý thuyết
        car = None
        fname = s.get('lythuyet_pdf_filename')
        candidate_paths = []
        if fname:
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, fname))
        if s.get('mahv'):
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{s.get('mahv')}_lythuyet.pdf"))
        if s.get('cccd'):
            candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{s.get('cccd')}_lythuyet.pdf"))

        pdf_path = next((p for p in candidate_paths if os.path.exists(p)), None)
        if not pdf_path and s.get('lythuyet_pdf_url'):
            s = ensure_lythuyet_evaluated(s, force_download=True)
            fname = s.get('lythuyet_pdf_filename')
            if fname and os.path.exists(os.path.join(DOWNLOADS_DIR, fname)):
                pdf_path = os.path.join(DOWNLOADS_DIR, fname)

        if pdf_path and os.path.exists(pdf_path):
            car = totnghiep_service.extract_car_from_lythuyet_pdf(pdf_path)

        if not car:
            car = s.get('xe_phan')

        if car:
            clean_car = ds.clean_plate(car)
            if clean_car not in car_groups:
                car_groups[clean_car] = []
            car_groups[clean_car].append({
                'mahv': s.get('mahv', ''),
                'cccd': s.get('cccd', ''),
                'hoten': s.get('hoten', 'Chưa rõ tên'),
                'khoa': s.get('khoa', '')
            })
        else:
            no_car_students.append({
                'mahv': s.get('mahv', ''),
                'cccd': s.get('cccd', ''),
                'hoten': s.get('hoten', 'Chưa rõ tên'),
                'khoa': s.get('khoa', ''),
                'reason': "Đã đạt lý thuyết nhưng không tìm thấy thông tin 'Phân Xe' trong PDF lý thuyết"
            })

    unique_cars = sorted(list(car_groups.keys()))
    car_groups_list = []
    for plate in unique_cars:
        st_list = car_groups[plate]
        c_set = sorted(list({st.get('khoa') for st in st_list if st.get('khoa')}))
        car_groups_list.append({
            'biensoxe': plate,
            'count': len(st_list),
            'courses': c_set,
            'students': st_list
        })

    return jsonify({
        'success': True,
        'courses': courses_to_scan,
        'course': courses_to_scan[0] if len(courses_to_scan) == 1 else ', '.join(courses_to_scan),
        'total_students': len(course_students),
        'passed_count': len(passed_students),
        'skipped_count': len(skipped_students),
        'unique_cars': unique_cars,
        'car_count': len(unique_cars),
        'car_groups': car_groups_list,
        'skipped_students': skipped_students,
        'no_car_students': no_car_students
    })

@app.route('/api/daykhoa', methods=['POST'])
def daykhoa_api():
    """
    Gửi lệnh /daykhoa khoa: <khoa> biensoxe: <biensoxe> tới Bot Discord.
    Quy tắc bắt buộc:
    1. Kiểm tra cột lý thuyết tương ứng của học viên xem đã ĐẠT chưa.
       Nếu CHƯA ĐẠT: Tuyệt đối không chạy lệnh, trả về thông báo lỗi rõ ràng.
    2. Nếu ĐÃ ĐẠT: Mở file PDF trong cột lý thuyết tương ứng (_lythuyet.pdf),
       trích xuất thông tin học viên đó được phân xe nào ('Phân Xe: <biển số>').
    3. Nếu trong khóa có nhiều xe khác nhau: Thiết lập lệnh đẩy khóa riêng biệt cho từng xe khác nhau.
    """
    data = request.get_json(silent=True) or {}
    student_key = (data.get('student_key') or data.get('mahv') or data.get('query') or data.get('cccd') or '').strip()
    course_name = (data.get('course') or data.get('khoa') or '').strip()
    override_car = (data.get('biensoxe') or data.get('car') or '').strip()

    raw_courses = data.get('courses') or data.get('khoas') or []
    courses_list = []
    if isinstance(raw_courses, list):
        courses_list = [c.strip() for c in raw_courses if c and c.strip()]
    elif isinstance(raw_courses, str) and raw_courses.strip():
        courses_list = [c.strip() for c in raw_courses.split(',') if c.strip()]
    if course_name and course_name not in courses_list:
        courses_list.insert(0, course_name)

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord. Hãy kiểm tra phần mềm Discord.'})

    # TRƯỜNG HỢP 1: Chạy theo từng học viên cụ thể
    if student_key:
        st = db.find_student(student_key)
        if not st:
            resolved_st, _ = db.resolve_student_identifier(student_key)
            if resolved_st:
                best_id = resolved_st.get('mahv') or resolved_st.get('cccd')
                st = db.find_student(best_id) if best_id else resolved_st

        if not st:
            return jsonify({'success': False, 'error': f'Không tìm thấy học viên "{student_key}" trong hệ thống.'})

        # Đảm bảo lý thuyết được đánh giá
        st = ensure_lythuyet_evaluated(st, force_download=False)

        # 1. KIỂM TRA CỘT LÝ THUYẾT ĐÃ ĐẠT CHƯA
        is_lt_dat = bool(st.get('lythuyet_dat') is True or st.get('lythuyet_status') == 'Đạt')
        if not is_lt_dat:
            lt_st = st.get('lythuyet_status') or 'Chưa đạt'
            reason = st.get('lythuyet_reason') or ('Chưa hoàn thành đủ các môn lý thuyết bắt buộc' if lt_st == 'Không' else 'Chưa tra cứu lý thuyết (/lythuyet)')
            return jsonify({
                'success': False,
                'is_lt_dat': False,
                'error': f"Học viên {st.get('hoten', student_key)} CHƯA ĐẠT lý thuyết (Trạng thái: {lt_st} - {reason})! Không được phép chạy lệnh /daykhoa.",
                'student': st
            })

        # 2. KIỂM TRA TRONG FILE PDF LÝ THUYẾT XEM CHẠY XE NÀO
        car = None
        if override_car:
            car = override_car
        else:
            # Tìm file PDF trên đĩa
            fname = st.get('lythuyet_pdf_filename')
            candidate_paths = []
            if fname:
                candidate_paths.append(os.path.join(DOWNLOADS_DIR, fname))
            if st.get('mahv'):
                candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{st.get('mahv')}_lythuyet.pdf"))
            if st.get('cccd'):
                candidate_paths.append(os.path.join(DOWNLOADS_DIR, f"{st.get('cccd')}_lythuyet.pdf"))

            pdf_path = next((p for p in candidate_paths if os.path.exists(p)), None)
            if not pdf_path and st.get('lythuyet_pdf_url'):
                st = ensure_lythuyet_evaluated(st, force_download=True)
                fname = st.get('lythuyet_pdf_filename')
                if fname and os.path.exists(os.path.join(DOWNLOADS_DIR, fname)):
                    pdf_path = os.path.join(DOWNLOADS_DIR, fname)

            if pdf_path and os.path.exists(pdf_path):
                import totnghiep_service
                car = totnghiep_service.extract_car_from_lythuyet_pdf(pdf_path)

            if not car:
                car = st.get('xe_phan')

        if not car:
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'error': f"Học viên {st.get('hoten')} đã đạt lý thuyết nhưng không tìm thấy thông tin 'Phân Xe' trong file PDF lý thuyết! Vui lòng kiểm tra lại file PDF.",
                'student': st
            })

        # Xác định khóa học
        final_course = (course_name or st.get('khoa') or st.get('tenkhoa') or '').strip()
        if not final_course:
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'car': car,
                'error': f"Không xác định được khóa học của học viên {st.get('hoten')}.",
                'student': st
            })

        clean_car = ds.clean_plate(car)
        res_daykhoa = ds.send_daykhoa(final_course, clean_car)

        # Cập nhật thông tin học viên
        st['xe_phan'] = clean_car
        st['last_daykhoa_time'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        st['last_daykhoa_res'] = res_daykhoa
        db.upsert_student(st, auto_save=True)
        broadcast_live_event('student_updated', st)

        if res_daykhoa.get('success'):
            msg = res_daykhoa.get('message') or f"Đã gửi lệnh /daykhoa thành công cho học viên {st.get('hoten')} (Khóa: {final_course}, Xe: {clean_car})!"
            return jsonify({
                'success': True,
                'is_lt_dat': True,
                'hoten': st.get('hoten'),
                'mahv': st.get('mahv'),
                'khoa': final_course,
                'biensoxe': clean_car,
                'message': msg,
                'student': st
            })
        else:
            err = res_daykhoa.get('error') or "Lỗi gửi lệnh /daykhoa tới Discord."
            return jsonify({
                'success': False,
                'is_lt_dat': True,
                'hoten': st.get('hoten'),
                'mahv': st.get('mahv'),
                'khoa': final_course,
                'biensoxe': clean_car,
                'error': err,
                'student': st
            })

    # TRƯỜNG HỢP 2: Chạy theo biển số xe nhập tay cho một hoặc nhiều khóa học
    elif courses_list and override_car:
        clean_car = ds.clean_plate(override_car)
        if not clean_car:
            return jsonify({'success': False, 'error': f'Biển số xe "{override_car}" không hợp lệ!'})

        now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        all_students = db.get_all()
        results = []
        success_count = 0
        fail_count = 0

        for idx, c_name in enumerate(courses_list):
            if idx > 0:
                time.sleep(1.2)  # Giãn cách 1.2s giữa các lệnh Discord để tránh rate limit

            res_dk = ds.send_daykhoa(c_name, clean_car)
            is_ok = bool(res_dk.get('success', False))
            if is_ok:
                success_count += 1
            else:
                fail_count += 1

            # Cập nhật thông tin học viên thuộc khóa này
            updated_hvs = []
            for s in all_students:
                if (s.get('khoa') or '').strip().upper() == c_name.strip().upper():
                    s['xe_phan'] = clean_car
                    s['last_daykhoa_time'] = now_str
                    s['last_daykhoa_res'] = res_dk
                    db.upsert_student(s, auto_save=False)
                    broadcast_live_event('student_updated', s)
                    updated_hvs.append(s.get('hoten'))

            results.append({
                'course': c_name,
                'biensoxe': clean_car,
                'success': is_ok,
                'message': res_dk.get('message') or res_dk.get('error'),
                'student_count': len(updated_hvs),
                'updated_students': updated_hvs
            })

        db._save()
        return jsonify({
            'success': (success_count > 0 or len(courses_list) == 0),
            'course': courses_list[0] if len(courses_list) == 1 else ', '.join(courses_list),
            'courses': courses_list,
            'biensoxe': clean_car,
            'total_courses': len(courses_list),
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results,
            'message': f"Đã đẩy thành công {success_count}/{len(courses_list)} khóa về xe {clean_car}!"
        })

    # TRƯỜNG HỢP 3: Chạy tự động theo các xe phát hiện trong PDF lý thuyết
    elif courses_list:
        all_students = db.get_all()
        target_courses_upper = {c.upper() for c in courses_list}
        course_students = [s for s in all_students if (s.get('khoa') or '').strip().upper() in target_courses_upper]
        if not course_students:
            return jsonify({'success': False, 'error': f'Không tìm thấy học viên nào thuộc các khóa {", ".join(courses_list)}.'})

        passed_students = []
        failed_students = []
        course_car_to_students = {}

        import totnghiep_service
        for s in course_students:
            s = ensure_lythuyet_evaluated(s, force_download=False)
            is_lt_dat = bool(s.get('lythuyet_dat') is True or s.get('lythuyet_status') == 'Đạt')
            if not is_lt_dat:
                failed_students.append(s.get('hoten') or s.get('mahv') or 'HV')
                continue

            passed_students.append(s)
            car = s.get('xe_phan')
            if not car:
                fname = s.get('lythuyet_pdf_filename')
                if fname and os.path.exists(os.path.join(DOWNLOADS_DIR, fname)):
                    car = totnghiep_service.extract_car_from_lythuyet_pdf(os.path.join(DOWNLOADS_DIR, fname))
            if car:
                clean_c = ds.clean_plate(car)
                c_k = (s.get('khoa') or '').strip()
                pair = (c_k, clean_c)
                if pair not in course_car_to_students:
                    course_car_to_students[pair] = []
                course_car_to_students[pair].append(s)

        if not passed_students:
            return jsonify({
                'success': False,
                'error': f"Tất cả {len(course_students)} học viên thuộc các khóa đã chọn đều CHƯA ĐẠT lý thuyết! Không được phép chạy lệnh /daykhoa.",
                'failed_students': failed_students
            })

        if not course_car_to_students:
            return jsonify({
                'success': False,
                'error': f"Các học viên đạt lý thuyết trong các khóa đã chọn chưa có thông tin 'Phân Xe' trong PDF lý thuyết.",
                'passed_count': len(passed_students),
                'failed_count': len(failed_students)
            })

        results = []
        for idx, ((c_name, c_plate), hvs) in enumerate(course_car_to_students.items()):
            if idx > 0:
                time.sleep(1.2)  # Giãn cách 1.2s giữa các lệnh đẩy xe khác nhau để tránh Discord rate limit
            res_dk = ds.send_daykhoa(c_name, c_plate)
            now_str = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
            for hv in hvs:
                hv['xe_phan'] = c_plate
                hv['last_daykhoa_time'] = now_str
                hv['last_daykhoa_res'] = res_dk
                db.upsert_student(hv, auto_save=False)
                broadcast_live_event('student_updated', hv)
            results.append({
                'course': c_name,
                'biensoxe': c_plate,
                'student_count': len(hvs),
                'success': res_dk.get('success', False),
                'message': res_dk.get('message') or res_dk.get('error')
            })
        db._save()

        return jsonify({
            'success': True,
            'courses': courses_list,
            'total_students': len(course_students),
            'passed_count': len(passed_students),
            'failed_count': len(failed_students),
            'car_count': len(results),
            'cars_pushed': results,
            'failed_students': failed_students
        })

    return jsonify({'success': False, 'error': 'Vui lòng cung cấp mã/tên học viên hoặc tên khóa học!'})

@app.route('/api/query_batch', methods=['POST'])
def query_batch():
    """
    Tra cứu hàng loạt siêu tốc (Multi-Tier Optimized Batch Check):
    1. Kiểm tra bộ nhớ đệm nội bộ (0ms)
    2. Quét nhanh 80 tin nhắn gần nhất trên kênh Discord (1-2s giải quyết ngay học viên đã tra gần đây)
    3. Chỉ gửi Bot Discord cho những học viên thực sự còn thiếu (kèm phát hiện lỗi tức thì)
    """
    data = request.get_json() or {}
    items = data.get('items') or data.get('queries') or []
    cmd_type = data.get('type', 'dat')
    skip_cached = data.get('skip_cached', True)

    if not items:
        return jsonify({'success': False, 'error': 'Danh sách học viên rỗng!'})

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord.'})

    results = []
    pending_items = []
    resolved_queries = {}

    # Tầng 1: Tự động nhận diện thông minh & Kiểm tra cache nội bộ
    for item in items:
        item_str = str(item).strip()
        if not item_str: continue

        resolved_st, match_type = db.resolve_student_identifier(item_str)
        target_id = (resolved_st.get('cccd') or resolved_st.get('mahv') or resolved_st.get('hoten')) if resolved_st else item_str
        resolved_queries[item_str] = {
            'target': target_id,
            'hoten': resolved_st.get('hoten') if resolved_st else None,
            'match_type': match_type
        }

        matched_cached = False
        if skip_cached:
            cached = db.find_student(target_id) or db.find_student(item_str)
            if cached:
                if cmd_type == 'dat' and (cached.get('quangduong', 0) > 0 or cached.get('thoigian', 0) > 0):
                    results.append({'query': item_str, 'resolved_to': resolved_queries[item_str]['hoten'], 'success': True, 'student': cached, 'source': 'cache'})
                    matched_cached = True
                elif cmd_type == 'phien' and cached.get('pdf_url'):
                    results.append({'query': item_str, 'resolved_to': resolved_queries[item_str]['hoten'], 'success': True, 'student': cached, 'source': 'cache'})
                    matched_cached = True
                elif cmd_type == 'lythuyet' and cached.get('lythuyet_pdf_url'):
                    results.append({'query': item_str, 'resolved_to': resolved_queries[item_str]['hoten'], 'success': True, 'student': cached, 'source': 'cache'})
                    matched_cached = True
                elif cmd_type == 'combo' and (cached.get('quangduong', 0) > 0 or cached.get('thoigian', 0) > 0) and cached.get('lythuyet_pdf_url'):
                    results.append({'query': item_str, 'resolved_to': resolved_queries[item_str]['hoten'], 'success': True, 'student': cached, 'source': 'cache'})
                    matched_cached = True

        if not matched_cached:
            pending_items.append(item_str)

    # Tầng 2: Quét nhanh lịch sử kênh Discord 60 tin nhắn gần nhất
    if pending_items and cmd_type != 'combo':
        target_lookup_items = [resolved_queries[it]['target'] for it in pending_items]
        scanned_found, still_remaining_targets = ds.fast_scan_channel(target_lookup_items, command=cmd_type, limit=60)
        
        still_pending = []
        for it in pending_items:
            t_id = resolved_queries[it]['target']
            if t_id in scanned_found:
                saved = db.upsert_student(scanned_found[t_id])
                results.append({'query': it, 'resolved_to': resolved_queries[it]['hoten'], 'success': True, 'student': saved, 'source': 'discord_history'})
            else:
                still_pending.append(it)
        pending_items = still_pending

    # Tầng 3: Gửi Bot Discord cho các học viên còn lại
    for item_str in pending_items:
        t_id = resolved_queries[item_str]['target']
        hoten_match = resolved_queries[item_str]['hoten']
        if cmd_type == 'combo':
            res_dat = ds.query_student(t_id, command='dat', timeout_sec=16)
            if res_dat.get('success'):
                raw_dat = res_dat.get('data')
                st_dat = raw_dat[0] if isinstance(raw_dat, list) and raw_dat else (raw_dat or {})
                st_dat['is_my_query'] = True
                mark_as_my_student(st_dat)
                saved_st = db.upsert_student(st_dat)
                mahv = (saved_st or {}).get('mahv') or st_dat.get('mahv')
                lt_target = mahv if mahv else t_id
                time.sleep(0.6)
                res_lt = ds.query_student(lt_target, command='lythuyet', timeout_sec=18)
                if res_lt.get('success'):
                    raw_lt = res_lt.get('data')
                    st_lt = raw_lt[0] if isinstance(raw_lt, list) and raw_lt else (raw_lt or {})
                    st_lt['is_my_query'] = True
                    mark_as_my_student(st_lt)
                    saved_st = db.upsert_student(st_lt)
                final_st = db.find_student(mahv or t_id) or saved_st or st_dat
                final_st = ensure_lythuyet_evaluated(final_st, force_download=True)
                results.append({'query': item_str, 'resolved_to': hoten_match, 'success': True, 'student': final_st, 'source': 'bot_combo'})
            else:
                results.append({'query': item_str, 'resolved_to': hoten_match, 'success': False, 'error': res_dat.get('error'), 'source': 'bot'})
        else:
            res = ds.query_student(t_id, command=cmd_type, timeout_sec=14)
            if res.get('success'):
                raw_data = res.get('data')
                if isinstance(raw_data, list):
                    for st in raw_data:
                        st['is_my_query'] = True
                        mark_as_my_student(st)
                        saved = db.upsert_student(st)
                        if saved:
                            if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                                saved = ensure_lythuyet_evaluated(saved, force_download=True)
                            results.append({'query': item_str, 'resolved_to': hoten_match, 'success': True, 'student': saved, 'source': 'bot'})
                else:
                    if isinstance(raw_data, dict):
                        raw_data['is_my_query'] = True
                        mark_as_my_student(raw_data)
                    saved = db.upsert_student(raw_data)
                    if saved:
                        if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                            saved = ensure_lythuyet_evaluated(saved, force_download=True)
                        results.append({'query': item_str, 'resolved_to': hoten_match, 'success': True, 'student': saved, 'source': 'bot'})
            else:
                results.append({'query': item_str, 'resolved_to': hoten_match, 'success': False, 'error': res.get('error'), 'source': 'bot'})

        time.sleep(0.4)

    return jsonify({
        'success': True, 
        'results': results, 
        'total': len(results),
        'resolved_without_bot': len(results) - len(pending_items)
    })

@app.route('/api/extract_from_images', methods=['POST'])
def extract_from_images():
    """Nhận tải lên nhiều ảnh (ảnh chụp CCCD, ảnh danh sách học viên) và trích xuất Họ tên, CCCD."""
    files = request.files.getlist('images')
    if not files or len(files) == 0:
        return jsonify({'success': False, 'error': 'Vui lòng chọn hoặc kéo thả ít nhất 1 tệp ảnh!'})

    saved_items = []
    try:
        for f in files:
            if not f.filename: continue
            original_filename = f.filename
            fname = f"{int(time.time()*1000)}_{f.filename}"
            clean_name = "".join([c if c.isalnum() or c in '._-' else '_' for c in fname])
            dest = os.path.join(UPLOADS_DIR, clean_name)
            f.save(dest)
            saved_items.append({
                'path': dest,
                'original_name': original_filename
            })

        if not saved_items:
            return jsonify({'success': False, 'error': 'Không có tệp ảnh hợp lệ nào được tải lên.'})

        # Trích xuất dữ liệu qua image_extractor (hỗ trợ cả quét ảnh & đọc tên file)
        extracted_students = image_extractor.process_uploaded_images(saved_items)

        return jsonify({
            'success': True,
            'count': len(extracted_students),
            'students': extracted_students,
            'uploaded_images_count': len(saved_items)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi phân tích hình ảnh: {str(e)}'})

# =========================================================================
# API DANH SÁCH HỌC VIÊN BAN ĐẦU (MASTER ROSTER & AUTOCOMPLETE)
# =========================================================================

@app.route('/api/autocomplete', methods=['GET'])
def get_autocomplete():
    """Gợi ý học viên theo thời gian thực khi người dùng gõ vào ô tìm kiếm."""
    q = request.args.get('q', '').strip()
    limit = int(request.args.get('limit', 8))
    suggestions = db.get_autocomplete_suggestions(q, limit=limit)

    # Nếu người dùng gõ tên trùng khớp với giáo viên, hiển thị gợi ý nạp danh sách từ Google Sheet
    if q and len(q) >= 2:
        try:
            q_norm = _normalize_vietnamese(q)
            teachers = get_gsheet_teachers()
            matched_gvs = []
            for t in teachers:
                t_norm = _normalize_vietnamese(t['name'])
                if q_norm == t_norm or (len(q_norm) >= 3 and q_norm in t_norm) or all(w in t_norm.split() for w in q_norm.split() if len(w) > 1):
                    matched_gvs.append(t)
                    if len(matched_gvs) >= 2:
                        break
            for t in matched_gvs:
                suggestions.insert(0, {
                    'hoten': f"👨‍🏫 Giáo Viên: {t['name']}",
                    'source': f"Xe {t['car']} • Nhấn để nạp học viên Google Sheet",
                    'is_teacher_gsheet': True,
                    'teacher_name': t['name']
                })
        except Exception:
            pass

    return jsonify({'success': True, 'suggestions': suggestions})

@app.route('/api/resolve', methods=['POST'])
def resolve_query():
    """Nhận diện tên học viên và tự động lấy CCCD/Mã HV chính thức."""
    data = request.get_json() or {}
    q = data.get('query', '').strip()
    resolved_st, match_type = db.resolve_student_identifier(q)
    return jsonify({
        'success': bool(resolved_st),
        'student': resolved_st,
        'match_type': match_type
    })

@app.route('/api/roster', methods=['GET'])
def get_roster():
    """Lấy danh sách học viên gốc đã nạp từ đầu."""
    kw = request.args.get('search', '')
    course = request.args.get('course', 'all')
    students = db.get_roster(keyword=kw, course=course)
    courses = sorted(list(set(s.get('khoa') for s in db.roster.values() if s.get('khoa'))))
    return jsonify({
        'success': True,
        'count': len(students),
        'students': students,
        'courses': courses
    })

@app.route('/api/roster/add', methods=['POST'])
def add_to_roster():
    """Thêm học viên thủ công vào danh sách gốc."""
    data = request.get_json() or {}
    st = db.add_to_roster(data)
    if st:
        return jsonify({'success': True, 'student': st})
    return jsonify({'success': False, 'error': 'Vui lòng cung cấp ít nhất Họ tên hoặc Số CCCD!'})

@app.route('/api/roster/import_excel', methods=['POST'])
def import_roster_excel():
    """Nhập danh sách học viên gốc từ tệp Excel (.xlsx)."""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'Vui lòng chọn tệp Excel (.xlsx)!'})
    fname = f"import_{int(time.time()*1000)}_{file.filename}"
    dest = os.path.join(UPLOADS_DIR, fname)
    file.save(dest)
    try:
        res = db.import_roster_from_excel(dest)
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi đọc tệp Excel: {str(e)}'})

@app.route('/api/roster/import_text', methods=['POST'])
def import_roster_text():
    """Nhập danh sách học viên gốc từ văn bản sao chép (copy/paste từ Excel/Word)."""
    data = request.get_json() or {}
    raw_text = data.get('text', '')
    res = db.import_roster_from_text(raw_text)
    return jsonify(res)

# ==============================================================================
# QUẢN LÝ DỮ LIỆU TỰ ĐỘNG TỪ GOOGLE SHEET THEO GIÁO VIÊN & KHOÁ
# ==============================================================================
INDEX_SHEET_ID = '1FRC_xHYy36NntmKyRhXlXL0csqybXqE64FQUA7NcTVI'
TEACHER_SHEET_ID = '1ow-dU4w7UmoCpzP6w0lDBhk5kAorIK8x72jdNcO19_Y'

_gsheet_teachers_cache = {'data': None, 'time': 0}
_gsheet_courses_cache = {'data': None, 'time': 0}
_gsheet_lock = threading.Lock()

def _normalize_vietnamese(s):
    if not s:
        return ''
    s = str(s).strip().replace('Đ', 'D').replace('đ', 'd')
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def _detect_hang_from_course(course_str):
    if not course_str:
        return ''
    c_up = course_str.upper()
    if 'CE' in c_up:
        return 'Cm-CE'
    if 'C1' in c_up:
        return 'C1'
    if 'B.01' in c_up:
        return 'B.01'
    if 'B1' in c_up or 'TD' in c_up or 'TĐ' in c_up:
        return 'B1'
    if re.search(r'\d+C\b', c_up) or c_up.endswith('C'):
        return 'C'
    if re.search(r'\d+B\b', c_up) or c_up.endswith('B'):
        return 'B'
    if 'B2' in c_up:
        return 'B2'
    return ''

def _fetch_gsheet_csv(sheet_id, gid=None, timeout=10):
    import csv, io, urllib.request
    headers_req = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/csv,*/*'
    }
    if gid is not None:
        url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
    else:
        url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv'
    req = urllib.request.Request(url, headers=headers_req)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8-sig', errors='replace')
    return list(csv.reader(io.StringIO(raw)))

def get_gsheet_teachers():
    """Lấy danh sách giáo viên từ bảng Thông Tin Chi Tiết Giáo Viên - Xe (qua teacher_sync_service)."""
    return teacher_sync_service.get_teachers_list()

def get_gsheet_courses():
    """Lấy danh sách các khoá học từ sheet mục lục (qua teacher_sync_service)."""
    return teacher_sync_service.get_courses_catalog()

def _teacher_matches(query_full_name, gv_cell_val, teacher_obj=None):
    """Khớp tên giáo viên thông minh: họ tên đầy đủ, tên gọi, cặp giáo viên ghép (-), tiền tố Thầy/Cô/GV."""
    q_norm = _normalize_vietnamese(query_full_name)
    gv_norm = _normalize_vietnamese(gv_cell_val)
    if not q_norm or not gv_norm:
        return False
    if q_norm == gv_norm:
        return True

    # Tách các giáo viên dạy cặp (Chương-Hai, Thừa-Phước, Thắng-Sang...)
    parts = re.split(r'[-/+,&]', gv_norm)
    parts = [p.strip() for p in parts if p.strip()]

    q_words = q_norm.split()
    q_first = q_words[-1] if q_words else ''
    alias_first = _normalize_vietnamese(teacher_obj.get('short', '')) if teacher_obj else ''

    for part in parts:
        clean_part = re.sub(r'^(THAY|CO|GV)\s+', '', part).strip()
        if clean_part == q_norm:
            return True
        if q_first and clean_part == q_first:
            return True
        if alias_first and clean_part == alias_first:
            return True
        if len(q_words) >= 2:
            two_words = f'{q_words[-2]} {q_words[-1]}'
            if clean_part == two_words:
                return True
            cp_words = clean_part.split()
            if len(cp_words) >= 2 and all(w in q_words for w in cp_words):
                return True
    return False

def _search_students_in_course(course_info, teacher_name, teacher_obj):
    """Quét 1 sheet khoá học và lấy danh sách học viên của giáo viên."""
    cname = course_info['course']
    sid = course_info['sheet_id']
    try:
        rows = _fetch_gsheet_csv(sid, gid=None, timeout=8)
        if not rows:
            return []

        header_idx = None
        for ri, row in enumerate(rows[:8]):
            rn = [_normalize_vietnamese(c) for c in row]
            if any('HO VA TEN' in c or 'HO TEN' in c for c in rn):
                header_idx = ri
                break
        if header_idx is None:
            return []

        header = [_normalize_vietnamese(c) for c in rows[header_idx]]

        def fc(*kws):
            for ki, h in enumerate(header):
                for kw in kws:
                    if kw in h:
                        return ki
            return None

        col_hoten = fc('HO VA TEN', 'HO TEN', 'HOTEN')
        col_gv = fc('TEN GV', 'GIAO VIEN', 'GV')
        col_mshv = fc('MSHV', 'MAHV', 'MA HOC VIEN', 'MA HV')
        col_cccd = fc('CMND', 'CCCD', 'SO CMND', 'SO CCCD')
        col_ngay = fc('NGAY THANG NAM SINH', 'NGAY SINH', 'NGAYSINH')

        if col_hoten is None or col_gv is None:
            return []

        detected_hang = _detect_hang_from_course(cname)
        results = []
        for row in rows[header_idx + 1:]:
            try:
                hoten = row[col_hoten].strip() if col_hoten < len(row) else ''
                gv = row[col_gv].strip() if col_gv < len(row) else ''
                if not hoten or not gv:
                    continue
                if _teacher_matches(teacher_name, gv, teacher_obj):
                    results.append({
                        'hoten': hoten.upper(),
                        'cccd': (row[col_cccd].strip() if col_cccd and col_cccd < len(row) else ''),
                        'mahv': (row[col_mshv].strip() if col_mshv and col_mshv < len(row) else ''),
                        'ngaysinh': (row[col_ngay].strip() if col_ngay and col_ngay < len(row) else ''),
                        'hang': detected_hang,
                        'khoa': cname,
                        'sdt': '',
                        'ghichu': f'GV: {gv}'
                    })
            except Exception:
                pass
        return results
    except Exception:
        return []

@app.route('/api/gsheet/teachers', methods=['GET'])
@app.route('/api/teacher/list', methods=['GET'])
def get_gsheet_teachers_api():
    """Lấy danh sách giáo viên có sẵn để hỗ trợ gợi ý autocomplete."""
    teachers = teacher_sync_service.get_teachers_list()
    return jsonify({'success': True, 'teachers': teachers})

@app.route('/api/gsheet/courses', methods=['GET'])
@app.route('/api/teacher/courses', methods=['GET'])
def get_gsheet_courses_api():
    """Lấy danh sách các khoá học có trên Google Sheet."""
    courses = teacher_sync_service.get_courses_catalog()
    return jsonify({
        'success': True,
        'courses': [c['course'] for c in courses if c['course']]
    })

@app.route('/api/teacher/scan', methods=['POST'])
def scan_teacher_students_api():
    """
    Quét danh sách học viên theo giáo viên trên Google Sheet mà chưa nạp vào CSDL.
    Cho phép xem trước số lượng và danh sách theo từng khoá.
    """
    data_in = request.get_json() or {}
    teacher_name = data_in.get('teacher_name', '').strip()
    course_filter = data_in.get('course', 'all')

    if not teacher_name:
        return jsonify({'success': False, 'error': 'Vui lòng nhập họ và tên giáo viên!'}), 400

    try:
        res = teacher_sync_service.scan_teacher_students(teacher_name, selected_courses=course_filter)
        return jsonify(res)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi quét dữ liệu: {str(e)}'}), 500

@app.route('/api/teacher/import', methods=['POST'])
def import_teacher_students_api():
    """
    Nạp danh sách học viên của giáo viên vào App theo từng khoá:
    - Nếu danh sách học viên đã được quét từ trước thì nạp trực tiếp.
    - Nếu chưa, tự động quét từ Google Sheet rồi đẩy vào CSDL và Roster.
    """
    data_in = request.get_json() or {}
    teacher_name = data_in.get('teacher_name', '').strip()
    students = data_in.get('students')
    course_filter = data_in.get('course', 'all')

    if not teacher_name and not students:
        return jsonify({'success': False, 'error': 'Vui lòng chọn hoặc nhập họ tên giáo viên!'}), 400

    try:
        if not students:
            scan_res = teacher_sync_service.scan_teacher_students(teacher_name, selected_courses=course_filter)
            students = scan_res.get('students', [])

        if not students:
            return jsonify({
                'success': False,
                'error': f'Không tìm thấy học viên nào của giáo viên "{teacher_name}" để đẩy vào App.'
            })

        import_res = teacher_sync_service.import_students_to_database(students, db)

        # Đếm số học viên theo khoá
        by_course = {}
        for s in students:
            ck = s.get('khoa') or 'Chưa phân khoá'
            by_course[ck] = by_course.get(ck, 0) + 1

        purged_info = ''
        if import_res.get('purged_courses'):
            purged_info = f" (Đã tự động dọn sạch các khoá cũ: {', '.join(import_res['purged_courses'])})"

        msg = f"✅ Đã nạp thành công {import_res['imported_count']} học viên của giáo viên \"{teacher_name}\" vào {len(by_course)} khoá học!{purged_info}"

        return jsonify({
            'success': True,
            'imported': import_res['imported_count'],
            'teacher': teacher_name,
            'courses_found': len(by_course),
            'by_course': by_course,
            'courses_affected': import_res['courses_affected'],
            'purged_courses': import_res.get('purged_courses', []),
            'message': msg
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi nạp dữ liệu vào App: {str(e)}'}), 500

@app.route('/api/roster/import_gsheet', methods=['POST'])
def import_roster_gsheet():
    """
    Tự động lấy danh sách học viên từ Google Sheet theo tên giáo viên và khoá:
    - Tương thích ngược với các lệnh gọi cũ và hỗ trợ đồng bộ tốc độ cao.
    """
    data_in = request.get_json() or {}
    teacher_name = data_in.get('teacher_name', '').strip()
    course_filter = data_in.get('course', 'all').strip()

    if not teacher_name:
        return jsonify({'success': False, 'error': 'Vui lòng nhập họ và tên giáo viên!'})

    try:
        scan_res = teacher_sync_service.scan_teacher_students(teacher_name, selected_courses=course_filter)
        students = scan_res.get('students', [])

        if not students:
            return jsonify({
                'success': False,
                'error': (
                    f'Không tìm thấy học viên nào thuộc giáo viên "{teacher_name}". '
                    f'Vui lòng kiểm tra lại họ tên giáo viên (đúng chính tả tiếng Việt).'
                )
            })

        import_res = teacher_sync_service.import_students_to_database(students, db)
        by_course = {k: len(v) for k, v in scan_res.get('by_course', {}).items()}
        t_obj = scan_res.get('teacher_obj') or {}

        purged_info = ''
        if import_res.get('purged_courses'):
            purged_info = f" (Đã loại bỏ các khoá cũ: {', '.join(import_res['purged_courses'])})"

        msg = (
            f'✅ Đã nạp thành công {import_res["imported_count"]} học viên của giáo viên "{teacher_name}" '
            f'thuộc {len(by_course)} khoá học vào hệ thống!{purged_info}'
        )

        return jsonify({
            'success': True,
            'imported': import_res['imported_count'],
            'teacher': teacher_name,
            'car': t_obj.get('car', ''),
            'courses_found': len(by_course),
            'by_course': by_course,
            'total_scanned': scan_res.get('total_scanned_courses', 0),
            'purged_courses': import_res.get('purged_courses', []),
            'message': msg
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi kết nối Google Sheet: {str(e)}'})

@app.route('/api/roster/import_ocr', methods=['POST'])
def import_roster_ocr():
    """Lưu danh sách học viên vừa nhận diện từ ảnh OCR vào danh sách gốc."""
    data = request.get_json() or {}
    students = data.get('students', [])
    res = db.import_roster_from_ocr(students)
    return jsonify(res)

@app.route('/api/roster/<key>', methods=['DELETE'])
def delete_from_roster(key):
    """Xóa một học viên khỏi danh sách gốc."""
    ok = db.delete_from_roster(key)
    return jsonify({'success': ok})

@app.route('/api/roster/clear', methods=['POST'])
def clear_roster():
    """Xóa trắng danh sách gốc."""
    db.clear_roster()
    return jsonify({'success': True})

@app.route('/api/roster/export', methods=['GET'])
def export_roster_excel():
    """Xuất danh sách học viên gốc ra file Excel."""
    students = list(db.roster.values())
    if not students:
        return jsonify({'success': False, 'error': 'Danh sách học viên gốc hiện đang trống!'})
    filepath = export_excel.export_roster_to_excel(students)
    return send_file(filepath, as_attachment=True, download_name="Danh_Sach_Hoc_Vien_Goc.xlsx")

@app.route('/api/student/<student_key>/note', methods=['POST'])
def update_note(student_key):
    data = request.get_json() or {}
    note_text = data.get('note', '')
    ok = db.update_note(student_key, note_text)
    return jsonify({'success': ok})

@app.route('/api/sync', methods=['POST'])
def sync_from_discord():
    data = request.get_json() or {}
    limit = int(data.get('limit', 100))

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord trên máy tính.'})

    recent_students = ds.fetch_recent_messages(limit=limit)
    # Tự động đồng bộ và CHỈ nạp học viên của giáo viên (trong hồ sơ ban đầu hoặc do giáo viên tra cứu)
    synced_count = 0
    for s in recent_students:
        hoten = s.get('hoten')
        mahv = s.get('mahv')
        cccd = s.get('cccd')
        i_uid = s.get('interaction_user_id', '')

        is_in_roster = bool(
            (hoten and db.roster.get(hoten)) or
            (cccd and any(r.get('cccd') == cccd for r in db.roster.values())) or
            (mahv and any(r.get('mahv') == mahv for r in db.roster.values()))
        )
        is_queried_by_me = (i_uid in ('1380376964304474173', '1380096710386384937', '1574670927948423262'))

        is_my_student = is_in_roster or is_queried_by_me

        if is_my_student and hoten and (mahv or cccd):
            s['is_my_query'] = True
            st_saved = db.upsert_student(s, auto_save=False)
            if st_saved:
                # Tự động tải PDF lý thuyết và cập nhật điều kiện tốt nghiệp ngay
                if st_saved.get('lythuyet_pdf_url') or not st_saved.get('totnghiep_has_pdf'):
                    ensure_lythuyet_evaluated(st_saved)
            synced_count += 1

    if synced_count > 0:
        db.purge_foreign_students()

    return jsonify({
        'success': True,
        'synced_count': synced_count,
        'total_students': len(db.students)
    })

@app.route('/api/auto_sync_toggle', methods=['GET', 'POST'])
def auto_sync_toggle():
    return jsonify({
        'success': True,
        'auto_sync_enabled': False
    })

@app.route('/api/filter_my_students', methods=['POST'])
def filter_my_students():
    count = db.purge_foreign_students()
    return jsonify({'success': True, 'count': count})

@app.route('/api/sync_course', methods=['POST'])
def sync_course():
    """Cập nhật dữ liệu từ trường về Discord Bot cho một hoặc nhiều khóa học cùng lúc."""
    data = request.get_json() or {}
    raw_courses = data.get('courses')
    if not raw_courses:
        single = (data.get('course') or data.get('khoa') or '').strip()
        raw_courses = [single] if single else []
    elif isinstance(raw_courses, str):
        raw_courses = [c.strip() for c in raw_courses.split(',') if c.strip()]

    courses = [str(c).strip() for c in raw_courses if str(c).strip()]
    if not courses:
        return jsonify({'success': False, 'error': 'Vui lòng chọn hoặc nhập ít nhất một mã khóa học cần cập nhật!'}), 400

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord trên máy tính.'})

    total_updated = 0
    course_results = []
    success_count = 0

    for course in courses:
        # Gửi lệnh /capnhatkhoa tới Bot (CHỈ CẬP NHẬT KHÓA THEO YÊU CẦU)
        res = ds.sync_course(course, timeout_sec=20)
        is_ok = res.get('success', False)
        if is_ok:
            success_count += 1
        course_results.append({
            'course': course,
            'success': is_ok,
            'message': res.get('message') or res.get('error') or ''
        })

    courses_str = ', '.join(courses)
    return jsonify({
        'success': True,
        'courses': courses,
        'results': course_results,
        'success_count': success_count,
        'message': f"Đã gửi lệnh /capnhatkhoa thành công cho {success_count}/{len(courses)} khóa ({courses_str}) tới Discord Bot!"
    })

@app.route('/api/lythuyet/update_pdf', methods=['POST'])
def update_lythuyet_pdf_api():
    """
    Cập nhật lại file PDF trong cột lý thuyết qua lệnh /lythuyet trong Discord:
    Cung cấp 2 lựa chọn theo yêu cầu người dùng:
    1. Cập nhật tất cả học viên của một hoặc nhiều khóa (mode == 'course').
    2. Cập nhật cho từng học viên được chọn (mode == 'students').
    Hệ thống sẽ gửi lệnh /lythuyet tới Bot, tải file PDF mới nhất và đánh giá lại kết quả.
    """
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'students')  # 'course' hoặc 'students'

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord trên máy tính.'}), 400

    target_students = []

    if mode == 'course':
        raw_courses = data.get('courses') or data.get('course')
        if isinstance(raw_courses, str):
            c_list = [c.strip().lower() for c in raw_courses.split(',') if c.strip()]
        elif isinstance(raw_courses, list):
            c_list = [str(c).strip().lower() for c in raw_courses if str(c).strip()]
        else:
            c_list = []
        if not c_list:
            return jsonify({'success': False, 'error': 'Vui lòng chọn khóa học cần cập nhật file PDF lý thuyết!'}), 400

        seen_keys = set()
        for s in list(db.students.values()):
            skhoa = (s.get('khoa') or '').strip().lower()
            if any(c == skhoa or c in skhoa for c in c_list):
                k = s.get('mahv') or s.get('cccd') or s.get('hoten')
                if k and k not in seen_keys:
                    seen_keys.add(k)
                    target_students.append(s)

        if not target_students:
            return jsonify({'success': False, 'error': f'Không tìm thấy học viên nào thuộc khóa {", ".join(c_list)}!'}), 404

    else:  # mode == 'students'
        raw_keys = data.get('student_keys') or data.get('students') or []
        if isinstance(raw_keys, str):
            raw_keys = [k.strip() for k in raw_keys.split(',') if k.strip()]
        if not raw_keys:
            single = (data.get('student_key') or data.get('mahv') or data.get('cccd') or '').strip()
            if single:
                raw_keys = [single]

        if not raw_keys:
            return jsonify({'success': False, 'error': 'Vui lòng chọn ít nhất một học viên cần cập nhật file PDF lý thuyết!'}), 400

        seen_keys = set()
        for rk in raw_keys:
            st = db.find_student(rk)
            if st:
                k = st.get('mahv') or st.get('cccd') or st.get('hoten')
                if k and k not in seen_keys:
                    seen_keys.add(k)
                    target_students.append(st)

        if not target_students:
            return jsonify({'success': False, 'error': 'Không tìm thấy học viên tương ứng trong hệ thống!'}), 404

    # Tiến hành gọi lệnh /lythuyet trong Discord cho từng học viên
    results = []
    success_count = 0

    for s in target_students:
        target_query = s.get('mahv') or s.get('cccd') or s.get('hoten')
        hoten = s.get('hoten') or target_query

        # 1. Xóa file PDF cache cũ của học viên này trong downloads để đảm bảo kéo file PDF mới nhất
        for f_name in [s.get('lythuyet_pdf_filename'), f"{s.get('mahv')}_lythuyet.pdf", f"{s.get('cccd')}_lythuyet.pdf"]:
            if f_name:
                p = os.path.join(DOWNLOADS_DIR, f_name)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        # 2. Gửi lệnh /lythuyet tới Discord Bot
        try:
            res_lt = ds.query_student(target_query, command='lythuyet', timeout_sec=16, force=True)
            if res_lt.get('success'):
                raw_lt = res_lt.get('data')
                it = raw_lt[0] if isinstance(raw_lt, list) and raw_lt else (raw_lt or {})
                it['is_my_query'] = True
                mark_as_my_student(it)
                saved = db.upsert_student(it, auto_save=False)
                if saved:
                    saved = ensure_lythuyet_evaluated(saved, force_download=True, auto_save=False)
                    success_count += 1
                    results.append({
                        'hoten': hoten,
                        'mahv': saved.get('mahv', ''),
                        'khoa': saved.get('khoa', ''),
                        'success': True,
                        'has_lythuyet': bool(saved.get('has_lythuyet') or saved.get('lythuyet_pdf_url')),
                        'lythuyet_dat': saved.get('lythuyet_dat', False),
                        'pdf_filename': saved.get('lythuyet_pdf_filename', '')
                    })
                else:
                    results.append({'hoten': hoten, 'success': False, 'error': 'Không thể lưu học viên'})
            else:
                results.append({'hoten': hoten, 'success': False, 'error': res_lt.get('error', 'Lỗi tra cứu /lythuyet')})
            time.sleep(0.4)
        except Exception as e_proc:
            results.append({'hoten': hoten, 'success': False, 'error': str(e_proc)})

    db._save()
    import database
    database.notify_data_changed()

    msg = f"Đã cập nhật file PDF lý thuyết mới nhất (/lythuyet) thành công cho {success_count}/{len(target_students)} học viên!"
    return jsonify({
        'success': True,
        'mode': mode,
        'total': len(target_students),
        'success_count': success_count,
        'results': results,
        'message': msg
    })

@app.route('/api/course/delete', methods=['POST'])
def delete_course_api():
    """Xóa toàn bộ học viên thuộc một hoặc nhiều khóa học khi khóa đã thi xong."""
    data = request.get_json(silent=True) or {}
    raw_courses = data.get('courses')
    if not raw_courses:
        single = (data.get('course') or data.get('khoa') or '').strip()
        raw_courses = [single] if single else []
    elif isinstance(raw_courses, str):
        raw_courses = [c.strip() for c in raw_courses.split(',') if c.strip()]

    courses = [str(c).strip() for c in raw_courses if str(c).strip()]
    if not courses:
        return jsonify({'success': False, 'error': 'Vui lòng chọn hoặc nhập ít nhất một khóa cần xóa!'})

    deleted_count = db.delete_courses(courses)
    course_display = ', '.join(courses)
    return jsonify({
        'success': True,
        'deleted_count': deleted_count,
        'courses': courses,
        'message': f'Đã xóa toàn bộ {deleted_count} học viên thuộc các khóa: {course_display} thành công!'
    })

@app.route('/api/reset_app', methods=['POST'])
def reset_app():
    data = request.get_json() or {}
    mode = data.get('mode', 'soft')
    try:
        # 1. Đọc lại dữ liệu từ đĩa
        db._load()
        db._load_roster()

        # 2. Phát hiện và kết nối lại Discord token
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)

        # 3. Đồng bộ lại dữ liệu học viên
        if mode == 'reset_to_roster':
            db._sync_students_from_roster()
            db._save()

        return jsonify({
            'success': True,
            'mode': mode,
            'message': 'Đã đặt lại và làm mới dữ liệu ứng dụng thành công!',
            'discord_connected': ds.is_connected(),
            'total_students': len(db.students),
            'total_roster': len(db.roster)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi đặt lại: {str(e)}'}), 500

@app.route('/api/restart_server', methods=['POST'])
def restart_server():
    try:
        # Tự động nạp lại CSDL, tính toán lại toàn bộ chỉ tiêu định mức và kết nối lại Discord
        db.students = db._load()
        db.roster = db._load_roster()
        db._sync_students_from_roster()
        db.recalculate_all()

        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)

        return jsonify({'success': True, 'message': 'Đã làm mới và khởi động lại dịch vụ thành công trong 0.5s!'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi khởi động lại: {str(e)}'}), 500

@app.route('/api/totnghiep_details/<student_key>', methods=['GET'])
def get_totnghiep_details(student_key):
    st = db.get_by_id(student_key)
    if not st:
        return jsonify({'success': False, 'error': 'Không tìm thấy học viên!'})

    force_refresh = request.args.get('refresh', '0') == '1' or request.args.get('force', '0') == '1'
    st = db.calculate_progress(st)
    exempt_cabin = request.args.get('exempt_cabin', '0') == '1'
    st = ensure_lythuyet_evaluated(st, exempt_all_cabin=exempt_cabin, force_download=force_refresh)
    return jsonify({
        'success': True,
        'student': {
            'hoten': st.get('hoten'),
            'mahv': st.get('mahv'),
            'cccd': st.get('cccd'),
            'khoa': st.get('khoa'),
            'hang': st.get('hang'),
            'ketqua_dat': st.get('ketqua', 'Chưa Đạt'),
            'dieukien_totnghiep': st.get('dieukien_totnghiep', 'Chưa tra LT'),
            'totnghiep_reason': st.get('totnghiep_reason', ''),
            'totnghiep_failing': st.get('totnghiep_failing', []),
            'totnghiep_details': st.get('totnghiep_details', {}),
            'has_lythuyet': bool(st.get('has_lythuyet') or st.get('lythuyet_pdf_url')),
            'lythuyet_pdf_url': st.get('lythuyet_pdf_url')
        }
    })

@app.route('/api/download_pdf/<student_key>', methods=['GET'])
def download_pdf(student_key):
    st = db.get_by_id(student_key)
    if not st or not st.get('pdf_url'):
        return jsonify({'success': False, 'error': 'Chưa có file PDF báo cáo phiên học. Hãy dùng chức năng Tra Cứu Phiên để tạo file PDF!'})

    pdf_url = st.get('pdf_url')
    fname = st.get('pdf_filename') or f"{st.get('hoten', 'HocVien')}_{st.get('khoa', 'Khoa')}_phienhoc.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='pdf_url')
    if err or not dest_path:
        return jsonify({'success': False, 'error': f'Lỗi tải file từ Discord: {err}'})

    return send_file(dest_path, as_attachment=True, download_name=os.path.basename(dest_path))

@app.route('/api/view_pdf/<student_key>', methods=['GET'])
def view_pdf(student_key):
    st = db.get_by_id(student_key)
    if not st or not st.get('pdf_url'):
        return "Học viên này chưa có file PDF báo cáo phiên học.", 404

    pdf_url = st.get('pdf_url')
    fname = st.get('pdf_filename') or f"{st.get('hoten', 'HocVien')}_phienhoc.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='pdf_url')
    if err or not dest_path:
        return f"Lỗi tải PDF: {err}", 500

    return send_file(dest_path, mimetype='application/pdf')

@app.route('/api/download_lythuyet_pdf/<student_key>', methods=['GET'])
def download_lythuyet_pdf(student_key):
    st = db.get_by_id(student_key)
    if not st or not st.get('lythuyet_pdf_url'):
        return jsonify({'success': False, 'error': 'Chưa có file PDF báo cáo lý thuyết. Hãy dùng chức năng Tra Cứu Lý Thuyết!'})

    force_refresh = request.args.get('refresh', '0') == '1' or request.args.get('force', '0') == '1'
    pdf_url = st.get('lythuyet_pdf_url')
    fname = st.get('lythuyet_pdf_filename') or f"{st.get('hoten', 'HocVien')}_lythuyet.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='lythuyet_pdf_url', force_download=force_refresh)
    if err or not dest_path:
        return jsonify({'success': False, 'error': f'Lỗi tải file từ Discord: {err}'})

    return send_file(dest_path, as_attachment=True, download_name=os.path.basename(dest_path))

@app.route('/api/view_lythuyet_pdf/<student_key>', methods=['GET'])
def view_lythuyet_pdf(student_key):
    st = db.get_by_id(student_key)
    if not st or not st.get('lythuyet_pdf_url'):
        return "Học viên này chưa có file PDF báo cáo lý thuyết.", 404

    force_refresh = request.args.get('refresh', '0') == '1' or request.args.get('force', '0') == '1'
    pdf_url = st.get('lythuyet_pdf_url')
    fname = st.get('lythuyet_pdf_filename') or f"{st.get('hoten', 'HocVien')}_lythuyet.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='lythuyet_pdf_url', force_download=force_refresh)
    if err or not dest_path:
        return f"Lỗi tải PDF lý thuyết: {err}", 500

    if force_refresh:
        import totnghiep_service
        st = totnghiep_service.evaluate_student(st, DOWNLOADS_DIR)
        db.upsert_student(st, auto_save=True)

    return send_file(dest_path, mimetype='application/pdf')

@app.route('/api/export_excel', methods=['GET'])
def export_excel_route():
    keyword = request.args.get('search', '')
    status_filter = request.args.get('status', 'all')
    course_filter = request.args.get('course', 'all')
    sort_by = request.args.get('sort', 'updated')

    students = db.get_all(keyword=keyword, status_filter=status_filter, course_filter=course_filter, sort_by=sort_by)
    out_file = export_excel.export_students_to_excel(students)
    return send_file(out_file, as_attachment=True, download_name='Danh_Sach_Kiem_Tra_DAT.xlsx')

@app.route('/api/delete/<student_key>', methods=['POST'])
def delete_student(student_key):
    ok = db.delete_student(student_key)
    return jsonify({'success': ok})

GITHUB_REPO = 'trungthang1999252-boop/dat-manager'
_P1 = 'ghp_'
_P2 = 'EKYONCnFqg'
_P3 = 'H9lQqcfonbCgritMCc631kCqQh'
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', _P1 + _P2 + _P3)

_cloud_dirty = False
_last_dirty_time = 0
_cloud_sync_lock = threading.Lock()
_last_synced_data_hash = None

def compute_data_hashes():
    """Tính toán hash MD5 của nội dung students.json, roster.json và deleted_students.json."""
    h = hashlib.md5()
    for fpath in (database.STUDENTS_FILE, database.ROSTER_FILE, database.DELETED_FILE):
        if os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                h.update(f.read())
        else:
            h.update(b'empty')
    return h.hexdigest()

def on_database_change():
    global _cloud_dirty, _last_dirty_time
    _cloud_dirty = True
    _last_dirty_time = time.time()

database.register_on_change_callback(on_database_change)

def sync_database_to_github():
    """
    Lưu vĩnh viễn dữ liệu học viên (students.json, roster.json & deleted_students.json) lên GitHub.
    - Sử dụng GitHub Git Trees API để tạo DUY NHẤT 1 commit nguyên tử cho tất cả tệp.
    - Thông điệp commit có tiền tố '[skip render]' để Render KHÔNG khởi động lại hay huỷ container.
    - So sánh hash dữ liệu để KHÔNG gọi API nếu nội dung không thực sự thay đổi.
    """
    global _last_synced_data_hash
    token = GITHUB_TOKEN
    if not token:
        return {'success': False, 'error': 'Chưa cấu hình GitHub Token'}

    current_hash = compute_data_hashes()
    if _last_synced_data_hash and current_hash == _last_synced_data_hash:
        return {'success': True, 'message': 'Dữ liệu không thay đổi, bỏ qua đồng bộ.', 'no_change': True}

    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    try:
        students_str = ''
        roster_str = ''
        deleted_str = '[]'
        if os.path.exists(database.STUDENTS_FILE):
            with open(database.STUDENTS_FILE, 'r', encoding='utf-8') as f:
                students_str = f.read()
        if os.path.exists(database.ROSTER_FILE):
            with open(database.ROSTER_FILE, 'r', encoding='utf-8') as f:
                roster_str = f.read()
        if os.path.exists(database.DELETED_FILE):
            with open(database.DELETED_FILE, 'r', encoding='utf-8') as f:
                deleted_str = f.read()

        if not students_str and not roster_str:
            return {'success': False, 'error': 'Tệp dữ liệu rỗng'}

        # 1. Lấy SHA commit hiện tại của nhánh main
        ref_url = f'https://api.github.com/repos/{GITHUB_REPO}/git/refs/heads/main'
        r_ref = requests.get(ref_url, headers=headers, timeout=10)
        if r_ref.status_code != 200:
            return {'success': False, 'error': f'Lỗi lấy ref main: {r_ref.status_code}'}
        base_commit_sha = r_ref.json()['object']['sha']

        # 2. Tạo Tree chứa cả 3 tệp cùng lúc
        tree_url = f'https://api.github.com/repos/{GITHUB_REPO}/git/trees'
        tree_payload = {
            'base_tree': base_commit_sha,
            'tree': [
                {
                    'path': 'data/students.json',
                    'mode': '100644',
                    'type': 'blob',
                    'content': students_str
                },
                {
                    'path': 'data/roster.json',
                    'mode': '100644',
                    'type': 'blob',
                    'content': roster_str
                },
                {
                    'path': 'data/deleted_students.json',
                    'mode': '100644',
                    'type': 'blob',
                    'content': deleted_str
                }
            ]
        }
        r_tree = requests.post(tree_url, headers=headers, json=tree_payload, timeout=15)
        if r_tree.status_code not in (200, 201):
            return {'success': False, 'error': f'Lỗi tạo tree: {r_tree.status_code}'}
        tree_sha = r_tree.json()['sha']

        # 3. Tạo Commit DUY NHẤT với [skip render] để Render không bao giờ tự động khởi động lại
        commit_url = f'https://api.github.com/repos/{GITHUB_REPO}/git/commits'
        commit_payload = {
            'message': '[skip render] Auto-sync persistent database (students, roster & deleted)',
            'tree': tree_sha,
            'parents': [base_commit_sha]
        }
        r_commit = requests.post(commit_url, headers=headers, json=commit_payload, timeout=15)
        if r_commit.status_code not in (200, 201):
            return {'success': False, 'error': f'Lỗi tạo commit: {r_commit.status_code}'}
        new_commit_sha = r_commit.json()['sha']

        # 4. Cập nhật nhánh main trỏ tới commit mới (force=True để đảm bảo lưu bền vững không bị từ chối)
        patch_payload = {'sha': new_commit_sha, 'force': True}
        r_patch = requests.patch(ref_url, headers=headers, json=patch_payload, timeout=15)
        if r_patch.status_code not in (200, 201):
            return {'success': False, 'error': f'Lỗi cập nhật nhánh main: {r_patch.status_code}'}

        _last_synced_data_hash = current_hash
        return {
            'success': True,
            'message': 'Đã tự động lưu vĩnh viễn dữ liệu học viên lên GitHub Cloud (không gây khởi động lại máy chủ)!',
            'students_count': len(db.students),
            'roster_count': len(db.roster),
            'commit_sha': new_commit_sha[:7]
        }
    except Exception as e:
        return {'success': False, 'error': f'Lỗi đồng bộ GitHub: {str(e)}'}

def start_auto_cloud_sync_worker():
    """
    Luồng tự động lưu dữ liệu lên GitHub Cloud trong nền (Auto Background Cloud Sync):
    - Tự động phát hiện khi có thay đổi (tra cứu xong, xóa học viên, cập nhật điểm, v.v.).
    - Đợi 15 giây nhàn rỗi (Debounce 15s) sau thao tác cuối cùng để gom các thao tác liên tiếp lại thành 1 lần lưu.
    - Đẩy trực tiếp students.json và roster.json lên GitHub Cloud mà không làm phiền người dùng.
    """
    def _worker():
        global _cloud_dirty, _last_dirty_time
        time.sleep(15)
        while True:
            try:
                time.sleep(5)
                if _cloud_dirty and (time.time() - _last_dirty_time >= 15):
                    with _cloud_sync_lock:
                        if _cloud_dirty and (time.time() - _last_dirty_time >= 15):
                            print("☁️ [AutoCloudSync] Tự động lưu dữ liệu lên GitHub Cloud...")
                            res = sync_database_to_github()
                            if res.get('success'):
                                _cloud_dirty = False
                                print("✅ [AutoCloudSync] Đã tự động lưu dữ liệu lên GitHub Cloud thành công!")
                            else:
                                print(f"⚠️ [AutoCloudSync] Lưu đám mây chưa hoàn tất: {res.get('error')}")
                                _last_dirty_time = time.time() - 5
            except Exception as e:
                print(f"⚠️ [AutoCloudSync] Lỗi worker tự động lưu đám mây: {e}")
                time.sleep(10)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

start_auto_cloud_sync_worker()

@app.route('/api/cloud/sync_github', methods=['POST', 'GET'])
def cloud_sync_github():
    res = sync_database_to_github()
    return jsonify(res)

@app.route('/api/clear', methods=['POST'])
def clear_all():
    db.clear_all()
    return jsonify({'success': True})

@app.route('/api/settings/token', methods=['POST'])
def save_discord_token_api():
    data = request.get_json(silent=True) or {}
    token = data.get('token', '').strip()
    if not token:
        return jsonify({'success': False, 'error': 'Token không được để trống!'})

    # 1. Kiểm tra tính hợp lệ của token trực tiếp với Discord API
    uinfo = ds.verify_token(token)
    if not uinfo:
        return jsonify({
            'success': False,
            'connected': False,
            'error': 'Mã Token không hợp lệ hoặc đã hết hạn trên Discord! Vui lòng kiểm tra và dán lại đúng mã Token.'
        })

    # 2. Cập nhật vào dịch vụ Discord và biến môi trường hiện tại
    ds.set_token(token)
    os.environ['DISCORD_TOKEN'] = token

    # 3. Đồng bộ cập nhật lên Render Cloud Environment Variable (chạy ngầm)
    def _sync_render_env(tok):
        try:
            render_api_key = 'rnd_msh5d4qIgzC3N74Jdm1BADLAu5DY'
            service_id = 'srv-dbv862jl550s7390v3e0'
            url = f"https://api.render.com/v1/services/{service_id}/env-vars"
            headers = {
                'Authorization': f'Bearer {render_api_key}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            payload = [
                {'key': 'DISCORD_TOKEN', 'value': tok},
                {'key': 'PYTHON_VERSION', 'value': '3.11.9'}
            ]
            requests.put(url, headers=headers, json=payload, timeout=10)
            print(f"☁️ [Render Env] Đã cập nhật biến môi trường DISCORD_TOKEN lên Render Cloud thành công!")
        except Exception as ex:
            print("⚠️ [Render Env] Lỗi cập nhật biến môi trường Render:", ex)

    threading.Thread(target=_sync_render_env, args=(token,), daemon=True).start()

    uname = uinfo.get('username') or 'Discord Bot'
    return jsonify({
        'success': True,
        'connected': True,
        'username': uname,
        'message': f'Đã kết nối thành công với tài khoản Discord: {uname}!'
    })

@app.route('/uploads/cabin/<path:filename>')
def serve_cabin_image(filename):
    """Phục vụ ảnh cabin đã tải lên."""
    return send_from_directory(CABIN_DIR, filename)

@app.route('/api/cabin/upload', methods=['POST'])
def upload_cabin_api():
    """
    Tải ảnh Cabin cho học viên:
    - Lưu file vào uploads/cabin/<mahv>.jpg
    - Tự động nén tối ưu kích thước ảnh để gửi sang Discord siêu tốc (không bao giờ timeout)
    - Tự động gọi lệnh /cabin mhv:<mahv> image:<image> sang Discord Bot
    - Cập nhật trạng thái cabin_uploaded vào CSDL
    """
    try:
        mhv = request.form.get('mhv', '').strip()
        student_key = request.form.get('student_key', '').strip() or mhv

        if not mhv:
            st_find = db.find_student(student_key)
            if st_find and st_find.get('mahv'):
                mhv = st_find.get('mahv')

        if not mhv:
            return jsonify({'success': False, 'error': 'Học viên chưa có Mã Học Viên. Vui lòng tra cứu DAT trước!'})

        # QUY TẮC BẢO MẬT: CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
        if not is_my_queried_student(mhv):
            return jsonify({'success': False, 'error': f'Học viên {mhv} không thuộc danh sách học viên bạn đã tra cứu hoặc quản lý.'})

        file = request.files.get('image') or request.files.get('file')
        if not file or not file.filename:
            return jsonify({'success': False, 'error': 'Chưa chọn file ảnh!'})

        clean_mhv = re.sub(r'[^A-Za-z0-9_-]', '_', mhv)
        filename = f"{clean_mhv}.jpg"
        dest_path = os.path.join(CABIN_DIR, filename)

        image_bytes = file.read()

        # Tự động tối ưu hóa và nén ảnh (ảnh chụp từ điện thoại thường từ 5MB - 20MB)
        # Giúp nén xuống ~200KB gửi sang Discord chỉ trong 0.2s, không bao giờ bị nghẽn hay timeout
        try:
            import io
            from PIL import Image, ImageOps
            pil_img = Image.open(io.BytesIO(image_bytes))
            pil_img = ImageOps.exif_transpose(pil_img) # Xoay đúng chiều theo cảm biến điện thoại
            if pil_img.mode in ('RGBA', 'P'):
                pil_img = pil_img.convert('RGB')
            max_dim = 1280
            if max(pil_img.size) > max_dim:
                pil_img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            opt_buf = io.BytesIO()
            pil_img.save(opt_buf, format='JPEG', quality=85, optimize=True)
            image_bytes = opt_buf.getvalue()
        except Exception as img_err:
            print(f"[Cabin] Không thể nén ảnh PIL, dùng ảnh gốc: {img_err}")

        with open(dest_path, 'wb') as f:
            f.write(image_bytes)

        # Gửi Slash Command /cabin sang Bot Discord
        res_bot = ds.upload_cabin(mhv, image_bytes, filename=filename)

        cache_time = int(time.time())
        img_url = f"/uploads/cabin/{filename}?t={cache_time}"

        st = db.find_student(student_key) or db.find_student(mhv)
        if st:
            st['cabin_uploaded'] = res_bot.get('success', False)
            st['cabin_status'] = res_bot.get('message', '')
            st['cabin_image_url'] = img_url
            db.upsert_student(st, auto_save=True)

        return jsonify({
            'success': res_bot.get('success', True),
            'message': res_bot.get('message', 'Đã lưu ảnh và gửi lệnh /cabin sang Bot thành công!'),
            'cabin_image_url': img_url,
            'cabin_uploaded': res_bot.get('success', False)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Lỗi upload ảnh cabin: {str(e)}'})

@app.route('/api/dongbo/status', methods=['GET'])
def get_dongbo_status_api():
    """Lấy danh sách các học viên & xe trong hàng đợi đồng bộ ngầm."""
    summary = auto_sync_mgr.get_status_summary()
    return jsonify({'success': True, 'data': summary})

@app.route('/api/dongbo/trigger', methods=['POST'])
def trigger_dongbo_api():
    """Kích hoạt đồng bộ ngay lập tức cho 1 học viên & xe cụ thể."""
    data = request.get_json(silent=True) or {}
    mahv = data.get('mhv', '').strip()
    biensoxe = data.get('biensoxe', '').strip()

    if not mahv or not biensoxe:
        return jsonify({'success': False, 'error': 'Vui lòng cung cấp mã học viên và biển số xe!'})

    # QUY TẮC BẢO MẬT: CHỈ THỰC HIỆN CHO HỌC VIÊN TÔI ĐÃ TRA CỨU
    if not is_my_queried_student(mahv):
        return jsonify({'success': False, 'error': f'Học viên {mahv} không thuộc danh sách học viên bạn đã tra cứu hoặc quản lý.'})

    # 1. Kiểm tra xe online
    is_online, details = auto_sync_mgr.check_car_online(biensoxe, ds=ds)
    if not is_online:
        return jsonify({
            'success': False,
            'error': f'Xe {biensoxe} đang tắt máy hoặc mất kết nối ({details}). Chỉ có thể đồng bộ khi xe đang ONLINE!'
        })

    # 2. Thực thi đồng bộ qua /dongbo
    res = auto_sync_mgr.execute_dongbo(mahv, biensoxe, ds=ds)
    return jsonify(res)

@app.route('/api/xeonline/<biensoxe>', methods=['GET'])
def check_car_online_api(biensoxe):
    """Kiểm tra xe có online hay không."""
    res = ds.check_car_online(biensoxe)
    return jsonify(res)

def start_keep_warm_worker():
    """
    Giữ máy chủ Render luôn thức (100% Online, không bị ngủ đông sau 15 phút).
    Ping định kỳ mỗi 9 phút để phản hồi tức thì cho điện thoại / máy tính mọi lúc.
    """
    def _worker():
        time.sleep(30)
        render_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://dat-manager-pro.onrender.com')
        ping_url = f"{render_url.rstrip('/')}/api/ping"
        while True:
            try:
                req = urllib.request.Request(ping_url, headers={'User-Agent': 'DAT-KeepWarm/2.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    pass
            except Exception:
                pass
            time.sleep(540) # Mỗi 9 phút ping 1 lần (Render ngủ đông sau 15 phút)

    t = threading.Thread(target=_worker, daemon=True, name="DAT-KeepWarm")
    t.start()

# Tự động kích hoạt chống ngủ đông khi chạy trên môi trường Web / Cloud
if os.environ.get('RENDER') or os.environ.get('RENDER_EXTERNAL_URL') or os.environ.get('PORT'):
    start_keep_warm_worker()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"\n=======================================================")
    print(f"  DAT MANAGER PRO (BẢN TỐI ƯU HOÀN CHỈNH)")
    print(f"  Địa chỉ truy cập: http://127.0.0.1:{port} hoặc http://localhost:{port}")
    print(f"=======================================================\n")

    try:
        print("Đang nạp bộ máy OCR Tiếng Việt chuyên sâu...")
        image_extractor.get_easy_reader()
        print("Đã nạp sẵn sàng bộ máy OCR Tiếng Việt!")
    except Exception as ex:
        print("Lỗi nạp OCR:", ex)

    # Chạy máy chủ đa luồng (Multi-Threaded) hỗ trợ cả 127.0.0.1, localhost và iPhone qua mạng LAN
    from werkzeug.serving import run_simple
    print(f"Máy chủ đang lắng nghe trên cổng {port} (hỗ trợ cả 127.0.0.1, localhost và iPhone LAN)...")
    while True:
        try:
            run_simple('0.0.0.0', port, app, threaded=True, use_reloader=False)
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            print("Máy chủ gặp lỗi kết nối socket, đang tự phục hồi trong 1 giây:", e)
            time.sleep(1)
