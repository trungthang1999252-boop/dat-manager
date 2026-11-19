import os
import sys
import threading
import json
import time
import urllib.request
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, Response

import discord_service
import database
import export_excel
import image_extractor

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['JSON_AS_ASCII'] = False
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024 * 1024 # Cho phép upload tối đa 128MB ảnh

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Khởi tạo Services
ds = discord_service.DiscordDATService()
init_token = discord_service.auto_detect_discord_token()
if init_token:
    ds.set_token(init_token)
db = database.StudentDatabase()

# Luồng tự động đồng bộ ngầm định kỳ với Discord
auto_sync_enabled = True

def start_auto_sync_worker():
    def _worker():
        time.sleep(2)  # Khởi động nhanh
        while True:
            try:
                if auto_sync_enabled and ds.is_connected():
                    # Lấy 15 tin nhắn mới nhất trên kênh Discord
                    recent = ds.fetch_recent_messages(limit=15)
                    updated = False
                    for s in recent:
                        cccd = s.get('cccd')
                        mahv = s.get('mahv')
                        hoten = s.get('hoten')
                        i_uid = s.get('interaction_user_id', '')

                        # Nhận diện:
                        # 1. Học viên đã có trong CSDL hoặc Roster
                        # 2. Hoặc học viên do chính giáo viên trên máy này (tranthang252) tra cứu trên Discord
                        # 3. Hoặc học viên được tra cứu từ tài khoản liên kết (trongnhan0000)
                        is_my_student = (
                            bool(db.find_student(cccd)) or 
                            bool(db.find_student(mahv)) or 
                            bool(db.find_student(hoten)) or
                            bool(db.roster.get(hoten)) or
                            i_uid in ('1380376964304474173', '1380096710386384937')
                        )

                        if is_my_student and (hoten or mahv or cccd):
                            st_saved = db.upsert_student(s, auto_save=False)
                            if st_saved and not st_saved.get('dieukien_totnghiep'):
                                ensure_lythuyet_evaluated(st_saved)
                            updated = True

                    if updated:
                        db._save()
            except Exception:
                pass
            time.sleep(4)  # Quét nhanh định kỳ mỗi 4 giây để phản hồi tức thì

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

start_auto_sync_worker()

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

@app.route('/')
def index():
    return render_template('index.html')

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

def fetch_pdf_file(pdf_url, fname, student_key=None, url_field=None):
    clean_fname = "".join([c if c.isalnum() or c in '._-' else '_' for c in fname])
    dest_path = os.path.join(DOWNLOADS_DIR, clean_fname)

    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1000:
        return dest_path, None

    target_url = pdf_url
    for attempt in range(2):
        try:
            req = urllib.request.Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                if len(data) > 500:
                    with open(dest_path, 'wb') as f:
                        f.write(data)
                    if student_key and url_field and target_url != pdf_url:
                        st = db.get_by_id(student_key)
                        if st:
                            st[url_field] = target_url
                            db._save()
                    return dest_path, None
        except Exception as e:
            if attempt == 0:
                # Làm mới link nếu bị hết hạn CDN signature
                refreshed = ds.refresh_attachment_url(pdf_url)
                if refreshed and refreshed != target_url:
                    target_url = refreshed
                    continue
            return None, str(e)

    return (dest_path, None) if os.path.exists(dest_path) else (None, "Không thể tải tệp")

def ensure_lythuyet_evaluated(student_obj, exempt_all_cabin=False):
    """Tự động tải file PDF lý thuyết về máy và chấm điểm điều kiện thi tốt nghiệp."""
    if not student_obj:
        return student_obj

    pdf_url = student_obj.get('lythuyet_pdf_url')
    fname = student_obj.get('lythuyet_pdf_filename') or f"{student_obj.get('mahv', 'HV')}_lythuyet.pdf"

    try:
        if pdf_url:
            st_key = student_obj.get('mahv') or student_obj.get('cccd')
            dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=st_key, url_field='lythuyet_pdf_url')
            if dest_path and os.path.exists(dest_path):
                student_obj['lythuyet_pdf_filename'] = os.path.basename(dest_path)
                student_obj['has_lythuyet'] = True
                import totnghiep_service
                student_obj = totnghiep_service.evaluate_student(student_obj, DOWNLOADS_DIR, exempt_all_cabin=exempt_all_cabin)
                db.upsert_student(student_obj, auto_save=True)
                return student_obj

        # Nếu đã có file sẵn trên ổ đĩa
        import totnghiep_service
        student_obj = totnghiep_service.evaluate_student(student_obj, DOWNLOADS_DIR, exempt_all_cabin=exempt_all_cabin)
        db.upsert_student(student_obj, auto_save=True)
    except Exception as e:
        print(f"Lỗi evaluate totnghiep: {e}")

    return student_obj

@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify(db.get_statistics())

@app.route('/api/students', methods=['GET'])
def get_students():
    keyword = request.args.get('search', '')
    status_filter = request.args.get('status', 'all')
    course_filter = request.args.get('course', 'all')
    sort_by = request.args.get('sort', 'updated')

    # Đảm bảo học viên được chấm điều kiện tốt nghiệp toàn diện (cả DAT và Lý thuyết)
    for s in list(db.students.values()):
        if not s.get('totnghiep_has_pdf') or 'dat_quangduong' not in s.get('totnghiep_details', {}):
            fname = s.get('lythuyet_pdf_filename') or (f"{s.get('mahv')}_lythuyet.pdf" if s.get('mahv') else '')
            p = os.path.join(DOWNLOADS_DIR, fname) if fname else ''
            if p and os.path.exists(p):
                ensure_lythuyet_evaluated(s)
            else:
                db.calculate_progress(s)
                db.upsert_student(s, auto_save=False)
    
    students = db.get_all(keyword=keyword, status_filter=status_filter, course_filter=course_filter, sort_by=sort_by)
    courses = sorted(list(set(s.get('khoa') for s in db.students.values() if s.get('khoa'))))
    return jsonify({
        'success': True,
        'count': len(students),
        'students': students,
        'courses': courses
    })

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

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord. Hãy kiểm tra phần mềm Discord.'})

    # 1. KHI BẤM /DAT HOẶC COMBO: Tra cứu DAT ➔ Tự động lấy Mã HV ➔ Tra cứu Lý thuyết (/lythuyet) ➔ Tự động tải PDF & Chấm điểm
    if cmd_type in ('dat', 'combo'):
        res_dat = ds.query_student(target_query, command='dat', timeout_sec=15)
        if not res_dat.get('success'):
            return jsonify({'success': False, 'error': res_dat.get('error', 'Lỗi tra cứu DAT.')})

        raw_dat = res_dat.get('data')
        st_dat = raw_dat[0] if isinstance(raw_dat, list) and raw_dat else (raw_dat or {})
        saved_st = db.upsert_student(st_dat, auto_save=True)

        mahv = (saved_st or {}).get('mahv') or st_dat.get('mahv')
        lt_target = mahv if mahv else ((saved_st or {}).get('cccd') or (saved_st or {}).get('hoten') or target_query)

        # TỰ ĐỘNG TRA CỨU TIẾP LÝ THUYẾT (/lythuyet) BẰNG MÃ HỌC VIÊN
        if lt_target:
            try:
                time.sleep(0.4)  # Giãn cách nhẹ tránh rate limit
                res_lt = ds.query_student(lt_target, command='lythuyet', timeout_sec=16)
                if res_lt.get('success'):
                    raw_lt = res_lt.get('data')
                    st_lt = raw_lt[0] if isinstance(raw_lt, list) and raw_lt else (raw_lt or {})
                    saved_st = db.upsert_student(st_lt, auto_save=True)
            except Exception as e_lt:
                print("Lỗi tự động tra cứu lý thuyết sau /dat:", e_lt)

        final_st = db.find_student(mahv or target_query) or saved_st or st_dat
        # TỰ ĐỘNG TẢI FILE PDF LÝ THUYẾT VỀ MÁY VÀ ĐÁNH GIÁ ĐIỀU KIỆN TỐT NGHIỆP
        final_st = ensure_lythuyet_evaluated(final_st)

        return jsonify({
            'success': True,
            'students': [final_st],
            'count': 1,
            'mahv': mahv,
            'hoten': (final_st or {}).get('hoten'),
            'has_lythuyet': bool((final_st or {}).get('has_lythuyet') or (final_st or {}).get('lythuyet_pdf_url')),
            'lythuyet_dat': (final_st or {}).get('lythuyet_dat', False),
            'dieukien_totnghiep': (final_st or {}).get('dieukien_totnghiep', 'Chưa tra LT'),
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
            saved = db.upsert_student(it, auto_save=True)
            if saved:
                saved = ensure_lythuyet_evaluated(saved)
                saved_list.append(saved)

        final_st = saved_list[0] if saved_list else db.find_student(target_query)
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

    res = ds.query_student(target_query, command=cmd_type)
    if res.get('success'):
        raw_data = res.get('data')
        saved_list = []
        if isinstance(raw_data, list):
            for st in raw_data:
                saved = db.upsert_student(st)
                if saved:
                    if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                        saved = ensure_lythuyet_evaluated(saved)
                    saved_list.append(saved)
        else:
            saved = db.upsert_student(raw_data)
            if saved:
                if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                    saved = ensure_lythuyet_evaluated(saved)
                saved_list.append(saved)

        return jsonify({'success': True, 'students': saved_list, 'count': len(saved_list), 'resolved': resolved_info})
    else:
        return jsonify({'success': False, 'error': res.get('error', 'Lỗi không xác định khi tra cứu.')})

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
        if cmd_type in ('combo', 'dat'):
            res_dat = ds.query_student(t_id, command='dat', timeout_sec=16)
            if res_dat.get('success'):
                raw_dat = res_dat.get('data')
                st_dat = raw_dat[0] if isinstance(raw_dat, list) and raw_dat else (raw_dat or {})
                saved_st = db.upsert_student(st_dat)
                mahv = (saved_st or {}).get('mahv') or st_dat.get('mahv')
                lt_target = mahv if mahv else t_id
                time.sleep(0.6)
                res_lt = ds.query_student(lt_target, command='lythuyet', timeout_sec=18)
                if res_lt.get('success'):
                    raw_lt = res_lt.get('data')
                    st_lt = raw_lt[0] if isinstance(raw_lt, list) and raw_lt else (raw_lt or {})
                    saved_st = db.upsert_student(st_lt)
                final_st = db.find_student(mahv or t_id) or saved_st or st_dat
                final_st = ensure_lythuyet_evaluated(final_st)
                results.append({'query': item_str, 'resolved_to': hoten_match, 'success': True, 'student': final_st, 'source': 'bot_combo'})
            else:
                results.append({'query': item_str, 'resolved_to': hoten_match, 'success': False, 'error': res_dat.get('error'), 'source': 'bot'})
        else:
            res = ds.query_student(t_id, command=cmd_type, timeout_sec=14)
            if res.get('success'):
                raw_data = res.get('data')
                if isinstance(raw_data, list):
                    for st in raw_data:
                        saved = db.upsert_student(st)
                        if saved:
                            if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                                saved = ensure_lythuyet_evaluated(saved)
                            results.append({'query': item_str, 'resolved_to': hoten_match, 'success': True, 'student': saved, 'source': 'bot'})
                else:
                    saved = db.upsert_student(raw_data)
                    if saved:
                        if cmd_type == 'lythuyet' or saved.get('lythuyet_pdf_url'):
                            saved = ensure_lythuyet_evaluated(saved)
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
    # Tự động đồng bộ và nạp tất cả học viên của giáo viên hoặc học viên vừa được tra cứu thành công từ Discord Bot về App
    synced_count = 0
    for s in recent_students:
        hoten = s.get('hoten')
        mahv = s.get('mahv')
        cccd = s.get('cccd')
        i_uid = s.get('interaction_user_id', '')

        is_my_student = (
            bool(db.find_student(cccd)) or 
            bool(db.find_student(mahv)) or 
            bool(db.find_student(hoten)) or
            bool(db.roster.get(hoten)) or
            i_uid in ('1380376964304474173', '1380096710386384937')
        )
        if is_my_student and hoten and (mahv or cccd):
            st_saved = db.upsert_student(s, auto_save=False)
            if st_saved:
                # Tự động tải PDF lý thuyết và cập nhật điều kiện tốt nghiệp ngay
                if st_saved.get('lythuyet_pdf_url') or not st_saved.get('totnghiep_has_pdf'):
                    ensure_lythuyet_evaluated(st_saved)
            synced_count += 1

    if synced_count > 0:
        db._save()

    return jsonify({
        'success': True,
        'synced_count': synced_count,
        'total_students': len(db.students)
    })

@app.route('/api/auto_sync_toggle', methods=['GET', 'POST'])
def auto_sync_toggle():
    global auto_sync_enabled
    if request.method == 'POST':
        data = request.get_json() or {}
        if 'enabled' in data:
            auto_sync_enabled = bool(data['enabled'])
        else:
            auto_sync_enabled = not auto_sync_enabled
    return jsonify({
        'success': True,
        'auto_sync_enabled': auto_sync_enabled
    })

@app.route('/api/sync_course', methods=['POST'])
def sync_course():
    data = request.get_json() or {}
    course = data.get('course', '').strip()
    if not course:
        return jsonify({'success': False, 'error': 'Vui lòng cung cấp mã khóa học cần cập nhật!'}), 400

    if not ds.is_connected():
        token = discord_service.auto_detect_discord_token()
        if token:
            ds.set_token(token)
        else:
            return jsonify({'success': False, 'error': 'Chưa kết nối được với Discord trên máy tính.'})

    res = ds.sync_course(course, timeout_sec=20)
    return jsonify(res)

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

    st = db.calculate_progress(st)
    exempt_cabin = request.args.get('exempt_cabin', '0') == '1'
    st = ensure_lythuyet_evaluated(st, exempt_all_cabin=exempt_cabin)
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

    pdf_url = st.get('lythuyet_pdf_url')
    fname = st.get('lythuyet_pdf_filename') or f"{st.get('hoten', 'HocVien')}_lythuyet.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='lythuyet_pdf_url')
    if err or not dest_path:
        return jsonify({'success': False, 'error': f'Lỗi tải file từ Discord: {err}'})

    return send_file(dest_path, as_attachment=True, download_name=os.path.basename(dest_path))

@app.route('/api/view_lythuyet_pdf/<student_key>', methods=['GET'])
def view_lythuyet_pdf(student_key):
    st = db.get_by_id(student_key)
    if not st or not st.get('lythuyet_pdf_url'):
        return "Học viên này chưa có file PDF báo cáo lý thuyết.", 404

    pdf_url = st.get('lythuyet_pdf_url')
    fname = st.get('lythuyet_pdf_filename') or f"{st.get('hoten', 'HocVien')}_lythuyet.pdf"
    dest_path, err = fetch_pdf_file(pdf_url, fname, student_key=student_key, url_field='lythuyet_pdf_url')
    if err or not dest_path:
        return f"Lỗi tải PDF lý thuyết: {err}", 500

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
    ds.set_token(token)
    return jsonify({
        'success': True,
        'connected': ds.is_connected(),
        'message': 'Đã lưu và cập nhật Discord Token thành công!'
    })

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
