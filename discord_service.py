import os
import sys
import re
import json
import time
import uuid
import base64
import ctypes
import unicodedata
import urllib.request
import urllib.parse
import requests

def strip_vietnamese_accents(s):
    if not s: return ''
    s = str(s).replace('Đ', 'D').replace('đ', 'd')
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

# Constants for Discord Server & Bot
GUILD_ID = "1100763300180017182"       # Trường CĐ Cơ điện - Xây dựng và Nông Lâm Trung Bộ
CHANNEL_ID = "1100763300180017185"     # Kênh tra cứu điều kiện DAT
APP_ID = "1376099961212502016"         # NLTB_Bot (BOT_KhaVy)

# Command Schemas
CMD_DAT_ID = "1382298230137688106"
CMD_DAT_VER = "1382298230137688107"

CMD_PHIEN_ID = "1380109102822330369"
CMD_PHIEN_VER = "1380115566928592976"

CMD_LYTHUYET_ID = "1380110945749172235"
CMD_LYTHUYET_VER = "1380115362783166514"

CMD_CAPNHATKHOA_ID = "1380114233689374722"
CMD_CAPNHATKHOA_VER = "1380114233689374723"

# Windows DPAPI decrypt (chỉ hỗ trợ trên Windows)
if sys.platform == 'win32':
    try:
        from ctypes import wintypes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

        def dpapi_decrypt(encrypted_bytes):
            crypt32 = ctypes.windll.crypt32
            blob_in = DATA_BLOB(len(encrypted_bytes), ctypes.cast(encrypted_bytes, ctypes.POINTER(ctypes.c_byte)))
            blob_out = DATA_BLOB()
            if crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                out_ptr = blob_out.pbData
                out_len = blob_out.cbData
                result = ctypes.string_at(out_ptr, out_len)
                ctypes.windll.kernel32.LocalFree(out_ptr)
                return result
            return None
    except Exception:
        def dpapi_decrypt(encrypted_bytes):
            return None
else:
    def dpapi_decrypt(encrypted_bytes):
        return None

def auto_detect_discord_token():
    """Tự động trích xuất token Discord hợp lệ (hỗ trợ ENV, cache JSON và Windows DPAPI)."""
    # 0. Ưu tiên biến môi trường DISCORD_TOKEN trên máy chủ Cloud
    env_token = os.environ.get('DISCORD_TOKEN', '').strip()
    if env_token:
        return env_token

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(base_dir, 'data', 'config.json')
    
    # 1. Thử lấy từ cache config.json
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cached_token = cfg.get('discord_token')
            if cached_token:
                req = urllib.request.Request(
                    'https://discord.com/api/v9/users/@me',
                    headers={'Authorization': cached_token, 'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        return cached_token
        except Exception:
            pass

    # 2. Quét LevelDB qua DPAPI (chỉ chạy trên Windows)
    if sys.platform != 'win32':
        return None

    # Danh sách các nguồn quét khả dĩ: Discord App, Edge, Chrome
    scan_targets = [
        # (local_state, leveldb_dir, is_discord_app)
        (os.path.expandvars(r'%APPDATA%\discord\Local State'), os.path.expandvars(r'%APPDATA%\discord\Local Storage\leveldb'), True),
        (os.path.expandvars(r'%APPDATA%\discordcanary\Local State'), os.path.expandvars(r'%APPDATA%\discordcanary\Local Storage\leveldb'), True),
        (os.path.expandvars(r'%APPDATA%\discordptb\Local State'), os.path.expandvars(r'%APPDATA%\discordptb\Local Storage\leveldb'), True),
        (os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Local State'), os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Local Storage\leveldb'), False),
        (os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Local State'), os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Local Storage\leveldb'), False),
    ]

    for local_state_path, leveldb_dir, is_discord_app in scan_targets:
        if not (os.path.exists(local_state_path) and os.path.exists(leveldb_dir)):
            continue
        try:
            with open(local_state_path, 'r', encoding='utf-8') as f:
                ls = json.load(f)
            raw_key = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
            master_key = dpapi_decrypt(raw_key)
            aesgcm = AESGCM(master_key)

            for fname in os.listdir(leveldb_dir):
                if not (fname.endswith('.ldb') or fname.endswith('.log')):
                    continue
                try:
                    with open(os.path.join(leveldb_dir, fname), 'rb') as f:
                        data = f.read()
                except Exception:
                    continue

                candidates = []
                # Nếu là Discord App: tìm dQw4w9WgXcQ:
                for m in re.findall(rb'dQw4w9WgXcQ:[^"\'\r\n\t ]+', data):
                    try:
                        payload = base64.b64decode(m.split(b'dQw4w9WgXcQ:')[1])
                        dec = aesgcm.decrypt(payload[3:15], payload[15:], None).decode('utf-8')
                        candidates.append(dec)
                    except Exception:
                        pass

                # Nếu là trình duyệt: tìm chuỗi token regex
                if not is_discord_app:
                    for m in re.findall(rb'[\w-]{24}\.[\w-]{6}\.[\w-]{27,}', data):
                        try:
                            candidates.append(m.decode('utf-8'))
                        except Exception:
                            pass

                for candidate in candidates:
                    try:
                        req = urllib.request.Request(
                            'https://discord.com/api/v9/users/@me',
                            headers={'Authorization': candidate, 'User-Agent': 'Mozilla/5.0'}
                        )
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            if resp.status == 200:
                                os.makedirs(os.path.join(base_dir, 'data'), exist_ok=True)
                                with open(config_file, 'w', encoding='utf-8') as cf:
                                    json.dump({'discord_token': candidate}, cf)
                                return candidate
                    except Exception:
                        pass
        except Exception:
            pass

    return None

class DiscordDATService:
    def __init__(self, token=None):
        self.token = token or auto_detect_discord_token()
        self.headers = {
            'Authorization': self.token or '',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        }
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter

        adapter = HTTPAdapter(
            pool_connections=25,
            pool_maxsize=50,
            max_retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504], raise_on_status=False)
        )
        self.session = requests.Session()
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self.session.headers.update(self.headers)
        self.latest_msg_id = None

    def set_token(self, token):
        self.token = token
        self.headers['Authorization'] = token
        self.session.headers['Authorization'] = token
        # Cập nhật cache
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(base_dir, 'data', 'config.json')
        os.makedirs(os.path.join(base_dir, 'data'), exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as cf:
            json.dump({'discord_token': token}, cf)

    def is_connected(self):
        if not self.token:
            return False
        try:
            resp = self.session.get('https://discord.com/api/v9/users/@me', timeout=4)
            return resp.status_code == 200
        except Exception:
            return False

    def get_user_info(self):
        if not self.token:
            return None
        try:
            resp = self.session.get('https://discord.com/api/v9/users/@me', timeout=4)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def parse_embed_data(self, embed, attachments=None):
        data = {
            'mahv': '',
            'hoten': '',
            'ngaysinh': '',
            'cccd': '',
            'hang': '',
            'khoa': '',
            'quangduong': 0.0,
            'quangduong_dat': False,
            'quangduong_thieu': 0.0,
            'thoigian': 0.0,
            'thoigian_dat': False,
            'thoigian_thieu': 0.0,
            'thoigian_dem': 0.0,
            'thoigian_dem_dat': False,
            'thoigian_dem_toithieu': 0.0,
            'thoigian_tudong': 0.0,
            'thoigian_tudong_dat': False,
            'suckhoe': 'Chưa có dữ liệu',
            'songay_str': '',
            'songay_dat': False,
            'lich_trinh_ngay': [],
            'so_phien_hople': 0,
            'so_phien_loi': 0,
            'ketqua': 'Chưa rõ',
            'pdf_url': None,
            'pdf_filename': None,
            'note': ''
        }

        for f in embed.get('fields', []):
            name = f.get('name', '').strip()
            val = f.get('value', '').strip()
            
            if 'Mã học viên' in name:
                clean_val = re.sub(r'[\*`]', '', val)
                data['mahv'] = clean_val.strip()
            elif 'Họ tên' in name:
                data['hoten'] = val.strip()
            elif 'Ngày sinh' in name:
                data['ngaysinh'] = val.strip()
            elif 'CMND/CCCD' in name:
                data['cccd'] = val.strip()
            elif 'Hạng đào tạo' in name:
                data['hang'] = val.strip()
            elif 'Khóa học' in name:
                data['khoa'] = val.strip()
            elif 'Tổng quãng đường' in name:
                km_m = re.search(r'([\d\.]+)\s*Km', val)
                if km_m:
                    data['quangduong'] = float(km_m.group(1))
                data['quangduong_dat'] = '✅' in val or 'Đủ' in val
                thieu_m = re.search(r'Còn thiếu:\s*([\d\.]+)', val)
                if thieu_m:
                    data['quangduong_thieu'] = float(thieu_m.group(1))
            elif 'Tổng thời gian' in name:
                h_m = re.search(r'([\d\.]+)\s*Giờ', val)
                if h_m:
                    data['thoigian'] = float(h_m.group(1))
                data['thoigian_dat'] = '✅' in val or 'Đủ' in val
                thieu_m = re.search(r'Còn thiếu:\s*([\d\.]+)', val)
                if thieu_m:
                    data['thoigian_thieu'] = float(thieu_m.group(1))
            elif 'Thời gian ban đêm' in name:
                h_m = re.search(r'([\d\.]+)\s*Giờ', val)
                if h_m:
                    data['thoigian_dem'] = float(h_m.group(1))
                data['thoigian_dem_dat'] = '✅' in val or 'Đủ' in val
                min_m = re.search(r'Tối thiểu:\s*([\d\.]+)', val)
                if min_m:
                    data['thoigian_dem_toithieu'] = float(min_m.group(1))
            elif 'Thời gian xe tự động' in name:
                h_m = re.search(r'([\d\.]+)\s*Giờ', val)
                if h_m:
                    data['thoigian_tudong'] = float(h_m.group(1))
                data['thoigian_tudong_dat'] = '✅' in val or 'Đủ' in val
            elif 'Sức khỏe' in name:
                data['suckhoe'] = val.strip()
            elif 'Số ngày thực hành' in name:
                data['songay_dat'] = '✅' in name or 'Đủ' in name
                day_m = re.search(r'\*\*(\d+/\d+\s*ngày)\*\*', val)
                if day_m:
                    data['songay_str'] = day_m.group(1)
                else:
                    data['songay_str'] = val.split('\n')[0].strip()
                lines = val.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('•'):
                        lm = re.search(r'•\s*(\d{2}/\d{2}/\d{4}):\s*([\d\.]+)\s*km\s*·\s*([\d\.]+)\s*h\s*·\s*(\d+)\s*phiên', line)
                        if lm:
                            data['lich_trinh_ngay'].append({
                                'ngay': lm.group(1),
                                'km': float(lm.group(2)),
                                'gio': float(lm.group(3)),
                                'phien': int(lm.group(4))
                            })
            elif 'Số phiên hợp lệ' in name:
                try: data['so_phien_hople'] = int(val.strip())
                except: pass
            elif 'Số phiên lỗi' in name:
                try: data['so_phien_loi'] = int(val.strip())
                except: pass
            elif 'Kết quả' in name:
                data['ketqua'] = 'Đạt' if 'Đạt' in val and 'Không' not in val else 'Không Đạt'

        if data['ketqua'] == 'Chưa rõ':
            if data['quangduong_dat'] and data['thoigian_dat'] and data['thoigian_dem_dat'] and data['songay_dat']:
                data['ketqua'] = 'Đạt'
            else:
                data['ketqua'] = 'Chưa Đạt'

        if embed.get('thumbnail') and embed['thumbnail'].get('url'):
            data['avatar_url'] = embed['thumbnail']['url']

        if attachments:
            for att in attachments:
                fname = att.get('filename', '')
                if 'lythuyet' in fname.lower() and fname.lower().endswith('.pdf'):
                    data['lythuyet_pdf_filename'] = fname
                    data['lythuyet_pdf_url'] = att.get('url')
                    data['has_lythuyet'] = True
                elif fname.lower().endswith('.pdf'):
                    data['pdf_filename'] = fname
                    data['pdf_url'] = att.get('url')

        return data

    def parse_discord_message(self, msg):
        """
        Phân tích toàn diện một tin nhắn từ Discord của NLTB_Bot:
        - /dat: Embed 'Thông tin học viên' hoặc 'Kiểm tra điều kiện DAT'
        - /phien: Embed 'Phiên học viên' + đính kèm file _phienhoc.pdf
        - /lythuyet: Plain text 'Báo cáo lý thuyết học viên...' + đính kèm file _lythuyet.pdf
        """
        if not msg or msg.get('author', {}).get('id') != APP_ID:
            return []

        results = []
        mid = msg.get('id')
        mtime = msg.get('timestamp')
        content = msg.get('content', '')
        embeds = msg.get('embeds', [])
        attachments = msg.get('attachments', [])

        phien_pdf = None
        lythuyet_pdf = None
        for att in attachments:
            fname = att.get('filename', '')
            furl = att.get('url', '')
            if 'lythuyet' in fname.lower() and fname.lower().endswith('.pdf'):
                lythuyet_pdf = {'filename': fname, 'url': furl}
            elif fname.lower().endswith('.pdf'):
                phien_pdf = {'filename': fname, 'url': furl}

        # 1. Tin nhắn có Embeds (/dat hoặc /phien)
        if embeds:
            for e in embeds:
                parsed = self.parse_embed_data(e, attachments)
                if phien_pdf:
                    parsed['pdf_filename'] = phien_pdf['filename']
                    parsed['pdf_url'] = phien_pdf['url']
                if lythuyet_pdf:
                    parsed['lythuyet_pdf_filename'] = lythuyet_pdf['filename']
                    parsed['lythuyet_pdf_url'] = lythuyet_pdf['url']
                    parsed['has_lythuyet'] = True
                if parsed.get('mahv') or parsed.get('hoten'):
                    parsed['msg_id'] = mid
                    parsed['msg_time'] = mtime
                    interaction = msg.get('interaction') or {}
                    i_user = interaction.get('user') or {}
                    parsed['interaction_user_id'] = str(i_user.get('id', ''))
                    parsed['interaction_username'] = str(i_user.get('username', ''))
                    results.append(parsed)

        # 2. Tin nhắn Plain text (/lythuyet không có embed)
        elif lythuyet_pdf or 'lý thuyết' in content.lower():
            hoten = ''
            mahv = ''
            m_name = re.search(r'lý thuyết học viên\s+([^\*\n\r]+)', content, re.IGNORECASE)
            if m_name:
                hoten = m_name.group(1).strip()

            fname_lt = lythuyet_pdf['filename'] if lythuyet_pdf else ''
            url_lt = lythuyet_pdf['url'] if lythuyet_pdf else ''

            if fname_lt:
                m_code = re.search(r'([A-Za-z0-9_-]+)_lythuyet\.pdf', fname_lt, re.IGNORECASE)
                if m_code:
                    mahv = m_code.group(1).strip()

            if hoten or mahv or url_lt:
                interaction = msg.get('interaction') or {}
                i_user = interaction.get('user') or {}
                data = {
                    'mahv': mahv,
                    'hoten': hoten,
                    'lythuyet_pdf_filename': fname_lt,
                    'lythuyet_pdf_url': url_lt,
                    'has_lythuyet': True,
                    'msg_id': mid,
                    'msg_time': mtime,
                    'interaction_user_id': str(i_user.get('id', '')),
                    'interaction_username': str(i_user.get('username', ''))
                }
                results.append(data)

        return results

    def fetch_recent_messages(self, limit=50):
        url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}"
        try:
            resp = self.session.get(url, timeout=6)
            if resp.status_code != 200:
                return []
            messages = resp.json()
            if messages and not self.latest_msg_id:
                self.latest_msg_id = messages[0]['id']
            students = []
            for msg in messages:
                parsed_list = self.parse_discord_message(msg)
                students.extend(parsed_list)
            return students
        except Exception as e:
            print("Lỗi fetch_recent_messages:", e)
            return []

    def fast_scan_channel(self, query_items, command='dat', limit=60):
        """
        Quét nhanh kênh Discord trong 1-2 giây để tìm học viên đã được tra cứu gần đây,
        giúp tiết kiệm hàng chục giây không cần gọi lại Bot.
        """
        found = {}
        try:
            recent_students = self.fetch_recent_messages(limit=limit)
            for item in query_items:
                q = str(item).strip().lower()
                for st in recent_students:
                    if (q == st.get('mahv', '').lower() or 
                        q == st.get('cccd', '').lower() or 
                        q == st.get('hoten', '').lower()):
                        if command == 'dat' and (st.get('quangduong', 0) > 0 or st.get('thoigian', 0) > 0):
                            found[item] = st
                            break
                        elif command == 'phien' and st.get('pdf_url'):
                            found[item] = st
                            break
                        elif command == 'lythuyet' and st.get('lythuyet_pdf_url'):
                            found[item] = st
                            break
        except Exception as e:
            print("Lỗi fast_scan_channel:", e)

        remaining = [item for item in query_items if item not in found]
        return found, remaining

    def query_student(self, query_text, command='dat', timeout_sec=16):
        """
        Gửi lệnh tra cứu (/dat, /phien, /lythuyet) với tốc độ tối ưu cao:
        1. Quét nhanh kênh Discord (Fast Cache): Nếu bot vừa mới trả lời học viên này gần đây,
           lấy ngay kết quả trong 0.1s không cần gọi lại bot!
        2. Tự động gửi Slash Command Interaction nếu chưa có trong kênh.
        3. Nhận diện thông minh phản hồi: Hỗ trợ cả trường hợp trùng tên nhiều khóa và tự động
           bắt tiếp thông tin khi chọn khóa trên Discord.
        """
        if command == 'dat':
            cmd_id, cmd_ver = CMD_DAT_ID, CMD_DAT_VER
        elif command == 'phien':
            cmd_id, cmd_ver = CMD_PHIEN_ID, CMD_PHIEN_VER
        elif command == 'lythuyet':
            cmd_id, cmd_ver = CMD_LYTHUYET_ID, CMD_LYTHUYET_VER
        else:
            cmd_id, cmd_ver = CMD_DAT_ID, CMD_DAT_VER

        q_clean = str(query_text).strip()
        q_lower = q_clean.lower()

        # BƯỚC 1: FAST SCAN & Lấy mốc ID tin nhắn mới nhất trong 1 lượt gọi duy nhất (tiết kiệm 300ms)
        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=12", timeout=3.5)
            if r_l.status_code == 200:
                raw_recent = r_l.json()
                if raw_recent and isinstance(raw_recent, list) and len(raw_recent) > 0:
                    latest_id = raw_recent[0].get('id')
                    self.latest_msg_id = latest_id

                    for m in raw_recent:
                        for st in self.parse_discord_message(m):
                            st_mahv = st.get('mahv', '').lower()
                            st_cccd = st.get('cccd', '').lower()
                            st_hoten = st.get('hoten', '').lower()

                            matched = False
                            if q_lower and (q_lower == st_mahv or q_lower == st_cccd or q_lower == st_hoten):
                                matched = True
                            elif q_lower and (q_lower in st_mahv or q_lower in st_hoten or (len(q_lower) >= 6 and q_lower in st_cccd)):
                                matched = True

                            if matched:
                                if command == 'dat' and (st.get('quangduong', 0) > 0 or st.get('thoigian', 0) > 0 or st.get('msg_id')):
                                    return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
                                elif command == 'phien' and st.get('pdf_url'):
                                    return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
                                elif command == 'lythuyet' and st.get('lythuyet_pdf_url'):
                                    return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
        except Exception:
            pass

        # BƯỚC 2: Gửi Slash Command Interaction tới Discord Bot
        interaction_url = 'https://discord.com/api/v9/interactions'
        payload = {
            'type': 2,
            'application_id': APP_ID,
            'guild_id': GUILD_ID,
            'channel_id': CHANNEL_ID,
            'session_id': uuid.uuid4().hex,
            'data': {
                'version': cmd_ver,
                'id': cmd_id,
                'name': command,
                'type': 1,
                'options': [
                    {
                        'type': 3,
                        'name': 'mhv',
                        'value': q_clean
                    }
                ]
            }
        }

        t_start = time.time()
        try:
            resp = self.session.post(interaction_url, json=payload, timeout=5)
            if resp.status_code not in (200, 204):
                print(f"[Discord Error {resp.status_code}] {resp.text}")
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code} ({resp.text[:100]})'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh: {str(e)}'}

        # BƯỚC 3: Chờ và bóc tách phản hồi Bot siêu tốc
        time.sleep(0.28)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=6"
        multi_courses_detected = None

        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.5)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                # Chấp nhận tin nhắn mới hơn latest_id hoặc mới được tạo sau thời điểm gửi lệnh
                if latest_id is None or int(mid) >= int(latest_id):
                    if m.get('author', {}).get('id') == APP_ID:
                        embeds = m.get('embeds', [])
                        first_embed = embeds[0] if embeds else {}
                        title = first_embed.get('title', '')
                        desc = first_embed.get('description', '')
                        content = m.get('content', '')
                        text_check = (title + ' ' + desc + ' ' + content).lower()

                        # 1. Phát hiện bot yêu cầu chọn khóa vì trùng tên
                        if 'chọn khóa' in text_check or 'nhiều học viên' in text_check:
                            courses_found = re.findall(r'•\s*\*\*([A-Za-z0-9_\.]+)\*\*', desc)
                            multi_courses_detected = courses_found
                            # Không dừng ngay, đợi tiếp vài giây để người dùng bấm chọn khóa trên Discord
                            continue

                        # 2. Phát hiện lỗi không tìm thấy
                        if '❌' in text_check and ('không tìm thấy' in text_check or 'dữ liệu trống' in text_check or 'lỗi' in text_check):
                            err_msg = re.sub(r'[\*`]', '', desc or content or title).strip()
                            self.latest_msg_id = mid
                            return {'success': False, 'error': err_msg or 'Không tìm thấy học viên này!'}

                        # 3. Bóc tách dữ liệu học viên hợp lệ
                        i_uid = m.get('interaction', {}).get('user', {}).get('id', '')
                        parsed_list = self.parse_discord_message(m)
                        matches = []
                        for st in parsed_list:
                            matched = False
                            st_mahv = st.get('mahv', '').lower()
                            st_hoten = st.get('hoten', '').lower()
                            st_cccd = st.get('cccd', '').lower()

                            # Chỉ chấp nhận nếu khớp với từ khóa tra cứu hoặc do chính tài khoản này gửi lệnh
                            is_query_match = (
                                (q_lower and q_lower in st_mahv) or 
                                (q_lower and q_lower in st_hoten) or 
                                (q_lower and q_lower in st_cccd) or
                                (st_hoten and q_lower and strip_vietnamese_accents(q_lower) in strip_vietnamese_accents(st_hoten))
                            )
                            is_my_interaction = (i_uid in ('1380376964304474173', '1380096710386384937'))

                            if is_query_match or is_my_interaction:
                                matched = True

                            if matched:
                                matches.append(st)

                        if matches:
                            self.latest_msg_id = mid
                            return {'success': True, 'data': matches}

            time.sleep(0.28)

        if multi_courses_detected:
            c_str = ", ".join(multi_courses_detected)
            return {'success': False, 'error': f'Trùng tên ở nhiều khóa ({c_str}). Vui lòng nhập số CCCD hoặc Mã học viên để tra cứu chính xác!'}

        return {'success': False, 'error': 'Bot phản hồi quá lâu hoặc không tìm thấy dữ liệu học viên này.'}

    def sync_course(self, course_name, timeout_sec=20):
        """
        Gửi lệnh /capnhatkhoa khoa: <course_name> tới Discord Bot để cập nhật
        dữ liệu thời lượng học lý thuyết, môn học TT14, cabin mới nhất từ Google Sheet/LMS của trường.
        """
        c_name = str(course_name).strip()
        if not c_name:
            return {'success': False, 'error': 'Tên khóa học không được để trống.'}

        # Lấy mốc ID tin nhắn mới nhất trước khi gửi lệnh
        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=4)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        interaction_url = 'https://discord.com/api/v9/interactions'
        payload = {
            'type': 2,
            'application_id': APP_ID,
            'guild_id': GUILD_ID,
            'channel_id': CHANNEL_ID,
            'session_id': uuid.uuid4().hex,
            'data': {
                'version': CMD_CAPNHATKHOA_VER,
                'id': CMD_CAPNHATKHOA_ID,
                'name': 'capnhatkhoa',
                'type': 1,
                'options': [
                    {
                        'type': 3,
                        'name': 'khoa',
                        'value': c_name
                    }
                ]
            }
        }

        t_start = time.time()
        try:
            resp = self.session.post(interaction_url, json=payload, timeout=5)
            if resp.status_code not in (200, 204):
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code}'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh: {str(e)}'}

        time.sleep(0.5)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=5"
        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=4)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    if m.get('author', {}).get('id') == APP_ID:
                        content = m.get('content', '')
                        desc = m.get('embeds', [{}])[0].get('description', '') if m.get('embeds') else ''
                        full_text = (content + ' ' + desc).strip()
                        if 'cập nhật khóa' in full_text.lower() or 'thành công' in full_text.lower() or '✅' in full_text:
                            self.latest_msg_id = mid
                            return {'success': True, 'message': full_text or f'Đã cập nhật khóa {c_name} thành công!'}
                        elif '❌' in full_text or 'không tìm thấy' in full_text.lower():
                            self.latest_msg_id = mid
                            return {'success': False, 'error': full_text}

            time.sleep(0.4)

        return {'success': True, 'message': f'Đã gửi yêu cầu cập nhật khóa {c_name} tới Bot.'}

    def refresh_attachment_url(self, url):
        """Làm mới link đính kèm Discord khi link cũ bị hết hạn chữ ký CDN."""
        if not url:
            return url
        try:
            r = self.session.post('https://discord.com/api/v9/attachments/refresh-urls', json={'attachment_urls': [url]}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                refreshed = data.get('refreshed_urls', [])
                if refreshed and refreshed[0].get('refreshed'):
                    return refreshed[0]['refreshed']
        except Exception as e:
            print("Lỗi refresh_attachment_url:", e)
        return url
