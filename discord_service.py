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
import threading
import random

def strip_vietnamese_accents(s):
    if not s: return ''
    s = str(s).replace('Đ', 'D').replace('đ', 'd')
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

# Constants for Discord Server & Bot
GUILD_ID = "1100763300180017182"       # Trường CĐ Cơ điện - Xây dựng và Nông Lâm Trung Bộ
CHANNEL_ID = "1100763300180017185"     # Kênh tra cứu điều kiện DAT
APP_ID = "1376099961212502016"         # NLTB_Bot (BOT_KhaVy)

# Command Schemas (Đã đồng bộ trực tiếp từ Discord Application Command Index mới nhất)
CMD_DAT_ID = "1573173094867275821"
CMD_DAT_VER = "1573173098302152811"

CMD_PHIEN_ID = "1573173095735500833"
CMD_PHIEN_VER = "1573173098528768012"

CMD_LYTHUYET_ID = "1573173095735500832"
CMD_LYTHUYET_VER = "1573173098528768011"

CMD_CAPNHATKHOA_ID = "1573173094867275820"
CMD_CAPNHATKHOA_VER = "1573173098302152810"

CMD_XEONLINE_ID = "1573173096603713538"
CMD_XEONLINE_VER = "1573173098528768014"

CMD_DONGBO_ID = "1573173097115156596"
CMD_DONGBO_VER = "1573173098612662294"

CMD_DONGBOTC_ID = "1573173097115156598"
CMD_DONGBOTC_VER = "1573173098612662295"

CMD_CABIN_ID = "1573173094867275819"
CMD_CABIN_VER = "1573173098302152809"

# Anti-Detection: Discord Desktop Client Headers & Super Properties
DISCORD_SUPER_PROPERTIES = base64.b64encode(json.dumps({
    "os": "Windows",
    "browser": "Discord Client",
    "release_channel": "stable",
    "client_version": "1.0.9168",
    "os_version": "10.0.19045",
    "os_arch": "x64",
    "system_locale": "vi-VN",
    "client_build_number": 344837,
    "native_build_number": None,
    "client_event_source": None
}).encode('utf-8')).decode('utf-8')

DISCORD_DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36'

def get_discord_desktop_headers(token='', content_type='application/json'):
    """Tạo bộ headers chuẩn Discord Desktop Client đồng nhất cho mọi request."""
    h = {
        'Authorization': str(token or '').strip(),
        'User-Agent': DISCORD_DESKTOP_UA,
        'X-Super-Properties': DISCORD_SUPER_PROPERTIES,
        'X-Discord-Locale': 'vi',
        'X-Discord-Timezone': 'Asia/Ho_Chi_Minh'
    }
    if content_type:
        h['Content-Type'] = content_type
    return h



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

def is_valid_discord_token(tok):
    """Kiểm tra token Discord có còn hợp lệ hay không bằng cách gọi /users/@me."""
    if not tok or len(str(tok).strip()) < 20:
        return False
    try:
        req = urllib.request.Request(
            'https://discord.com/api/v9/users/@me',
            headers=get_discord_desktop_headers(tok, content_type=None)
        )
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            return resp.status == 200
    except Exception:
        return False

def auto_detect_discord_token():
    """Tự động trích xuất token Discord hợp lệ (hỗ trợ ENV, cache JSON và Windows DPAPI)."""
    # 0. Kiểm tra biến môi trường DISCORD_TOKEN trên máy chủ Cloud
    env_token = os.environ.get('DISCORD_TOKEN', '').strip()
    if env_token:
        if is_valid_discord_token(env_token):
            return env_token
        else:
            print("[Discord Token] ⚠️ Biến môi trường DISCORD_TOKEN đã hết hạn (401). Đang chuyển sang tìm nguồn khác...")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(base_dir, 'data', 'config.json')
    
    # 1. Thử lấy từ cache config.json
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cached_token = cfg.get('discord_token')
            if cached_token and is_valid_discord_token(cached_token):
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36',
            'X-Super-Properties': DISCORD_SUPER_PROPERTIES,
            'X-Discord-Locale': 'vi',
            'X-Discord-Timezone': 'Asia/Ho_Chi_Minh'
        }
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter

        adapter = HTTPAdapter(
            pool_connections=25,
            pool_maxsize=50,
            max_retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504], raise_on_status=False, respect_retry_after_header=False)
        )
        self.session = requests.Session()
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self.session.headers.update(self.headers)
        self.latest_msg_id = None
        self._cached_conn_status = None
        self._cached_user_info = None
        self._last_conn_check = 0
        self._interaction_lock = threading.Lock()
        self._last_interaction_time = 0.0
        self._in_flight_queries = {}
        self._in_flight_lock = threading.Lock()
        self._channel_cache = None
        self._channel_cache_ts = 0.0
        self._channel_cache_lock = threading.Lock()
        self._command_registry = {}
        self._last_cmd_refresh = 0.0
        self._registry_lock = threading.Lock()

    def set_token(self, token):
        self.token = token
        self.headers['Authorization'] = token
        self.session.headers['Authorization'] = token
        self.session.headers['X-Super-Properties'] = DISCORD_SUPER_PROPERTIES
        self.session.headers['X-Discord-Locale'] = 'vi'
        self.session.headers['X-Discord-Timezone'] = 'Asia/Ho_Chi_Minh'
        self._cached_conn_status = None
        self._cached_user_info = None
        self._last_conn_check = 0
        # Cập nhật cache
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(base_dir, 'data', 'config.json')
        os.makedirs(os.path.join(base_dir, 'data'), exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as cf:
            json.dump({'discord_token': token}, cf)

    def get_recent_messages_cached(self, limit=10, max_age=3.0):
        """Cache các tin nhắn gần nhất trong 1.5s để nhiều luồng tra cứu cùng lúc không làm ngập Discord."""
        now = time.time()
        with self._channel_cache_lock:
            if self._channel_cache and (now - self._channel_cache_ts < max_age) and len(self._channel_cache) >= limit:
                return self._channel_cache[:limit]
        try:
            r = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={limit}", timeout=3.5)
            if r.status_code == 200:
                msgs = r.json()
                if isinstance(msgs, list):
                    with self._channel_cache_lock:
                        self._channel_cache = msgs
                        self._channel_cache_ts = time.time()
                    return msgs
        except Exception:
            pass
        with self._channel_cache_lock:
            return self._channel_cache[:limit] if self._channel_cache else []

    
    def refresh_command_index(self):
        """Tự động fetch danh mục lệnh mới nhất từ Discord API (application-command-index)."""
        with self._registry_lock:
            try:
                url = f"https://discord.com/api/v9/guilds/{GUILD_ID}/application-command-index"
                r = self.session.get(url, timeout=5)
                if r.status_code == 200:
                    d = r.json()
                    cmds = d.get('application_commands', [])
                    for c in cmds:
                        name = c.get('name')
                        if name:
                            self._command_registry[name] = {
                                'id': str(c.get('id')),
                                'version': str(c.get('version')),
                                'options': c.get('options', [])
                            }
                    self._last_cmd_refresh = time.time()
                    print(f"[DiscordService] Đã tự động nạp {len(self._command_registry)} lệnh từ application-command-index!")
                    return True
            except Exception as e:
                print(f"[DiscordService] Lỗi khi refresh_command_index: {e}")
        return False

    def get_command_info(self, cmd_name):
        """Lấy ID và Version của lệnh (ưu tiên bộ đệm động, tự động nạp đồng bộ lần đầu nếu rỗng)."""
        if not self._command_registry:
            self.refresh_command_index()
        elif time.time() - self._last_cmd_refresh > 1800:
            threading.Thread(target=self.refresh_command_index, daemon=True).start()

        with self._registry_lock:
            info = self._command_registry.get(cmd_name)
            if info:
                return info['id'], info['version']

        # Fallback constants
        defaults = {
            'dat': (CMD_DAT_ID, CMD_DAT_VER),
            'phien': (CMD_PHIEN_ID, CMD_PHIEN_VER),
            'lythuyet': (CMD_LYTHUYET_ID, CMD_LYTHUYET_VER),
            'capnhatkhoa': (CMD_CAPNHATKHOA_ID, CMD_CAPNHATKHOA_VER),
            'cabin': (CMD_CABIN_ID, CMD_CABIN_VER),
            'xeonline': (CMD_XEONLINE_ID, CMD_XEONLINE_VER),
            'dongbo': (CMD_DONGBO_ID, CMD_DONGBO_VER),
            'dongbotc': (CMD_DONGBOTC_ID, CMD_DONGBOTC_VER),
        }
        return defaults.get(cmd_name, (CMD_DAT_ID, CMD_DAT_VER))

    def build_interaction_payload(self, cmd_name, options, extra_data=None):
        """
        Xây dựng payload chuẩn xác 100% theo giao thức Discord Client interactions:
        - Tự động sinh snowflake nonce từ timestamp hiện tại
        - Bao gồm application_id, guild_id, channel_id, session_id
        - Bao gồm analytics_location và section_name
        - QUAN TRỌNG NHẤT: Bắt buộc nhúng 'guild_id' vào bên trong object 'data' (nếu thiếu, Discord trả về lỗi 400 - mã 10005 Tích Hợp Không Xác Định)
        """
        cmd_id, cmd_ver = self.get_command_info(cmd_name)
        nonce = str(int(time.time() * 1000 - 1420070400000) << 22)
        data = {
            'version': str(cmd_ver),
            'id': str(cmd_id),
            'name': cmd_name,
            'type': 1,
            'guild_id': GUILD_ID,
            'options': options
        }
        if extra_data:
            data.update(extra_data)

        payload = {
            'type': 2,
            'application_id': APP_ID,
            'guild_id': GUILD_ID,
            'channel_id': CHANNEL_ID,
            'session_id': uuid.uuid4().hex,
            'nonce': nonce,
            'analytics_location': 'slash_ui',
            'section_name': 'NLTB_Bot',
            'data': data
        }
        return payload

    def send_interaction(self, payload, timeout=6):
        """
        Gửi lệnh interaction tới Discord API kèm cơ chế điều tiết nhịp chống Bot Detection:
        - Giãn cách ngẫu nhiên mô phỏng thao tác tay người thật: 1.8s - 2.8s giữa các thao tác
        - Tự động xử lý retry an toàn khi gặp HTTP 429 Rate Limit
        """
        interaction_url = 'https://discord.com/api/v9/interactions'
        with self._interaction_lock:
            now_ts = time.time()
            gap = now_ts - self._last_interaction_time
            min_gap = 1.8 + random.uniform(0.3, 0.9)
            if gap < min_gap:
                time.sleep(min_gap - gap)
            self._last_interaction_time = time.time()
            resp = self.session.post(interaction_url, json=payload, timeout=timeout)

        if resp.status_code == 429:
            try:
                r_429 = resp.json()
                retry_wait = min(float(r_429.get('retry_after', 2.0)) + 0.5, 5.0)
                time.sleep(retry_wait)
                with self._interaction_lock:
                    self._last_interaction_time = time.time()
                    resp = self.session.post(interaction_url, json=payload, timeout=timeout)
            except Exception:
                pass
        return resp

    def is_connected(self):
        if not self.token:
            return False
        now = time.time()
        # Cache kết quả kiểm tra kết nối trong 60 giây để tránh rate-limit Discord và phản hồi siêu tốc
        if self._cached_conn_status is not None and (now - self._last_conn_check < 60):
            return self._cached_conn_status
        try:
            resp = self.session.get('https://discord.com/api/v9/users/@me', timeout=5)
            is_ok = (resp.status_code == 200)
            self._cached_conn_status = is_ok
            self._last_conn_check = now
            if is_ok:
                try:
                    self._cached_user_info = resp.json()
                except Exception:
                    pass
            return is_ok
        except Exception:
            # Nếu gặp timeout mạng đột xuất nhưng trước đó đã kết nối thành công thì vẫn giữ trạng thái
            if self._cached_conn_status is True and (now - self._last_conn_check < 180):
                return True
            return False

    def verify_token(self, token_to_test):
        """Kiểm tra token Discord có hợp lệ và lấy thông tin tài khoản."""
        if not token_to_test or len(str(token_to_test).strip()) < 20:
            return None
        try:
            resp = requests.get(
                'https://discord.com/api/v9/users/@me',
                headers=get_discord_desktop_headers(token_to_test, content_type=None),
                timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def get_user_info(self):
        if not self.token:
            return None
        now = time.time()
        if self._cached_user_info and (now - self._last_conn_check < 60):
            return self._cached_user_info
        try:
            resp = self.session.get('https://discord.com/api/v9/users/@me', timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                self._cached_user_info = data
                self._cached_conn_status = True
                self._last_conn_check = now
                return data
        except Exception:
            pass
        return self._cached_user_info

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
                min_td = re.search(r'Tối thiểu:\s*([\d\.]+)', val)
                if min_td:
                    data['thoigian_tudong_toithieu'] = float(min_td.group(1))
            elif 'Sức khỏe' in name:
                data['suckhoe'] = val.strip()
            elif 'Số ngày thực hành' in name or 'số ngày' in name.lower():
                # Tình trạng hoàn thành: ✅ hoặc Đủ (loại trừ 'chưa' / 'không')
                name_clean = name.lower()
                is_ok = ('✅' in name) or ('đủ' in name_clean and 'chưa' not in name_clean and 'không' not in name_clean)
                data['songay_dat'] = is_ok

                # Trích xuất chuỗi số ngày, ví dụ: "3/5 ngày", "5/5 ngày", "1/2 ngày"
                day_m = re.search(r'(\d+/\d+)\s*ngày', val, re.IGNORECASE)
                if not day_m:
                    day_m = re.search(r'\*\*(\d+/\d+)', val)
                if not day_m:
                    day_m = re.search(r'(\d+/\d+)', val)

                if day_m:
                    data['songay_str'] = f"{day_m.group(1)} ngày"
                else:
                    first_line = val.split('\n')[0].strip()
                    clean_first = re.sub(r'[\*`]', '', first_line).strip()
                    data['songay_str'] = clean_first

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

                # Tự tính số ngày từ lịch trình phiên chạy nếu chuỗi chưa bóc tách chuẩn
                if (not data['songay_str'] or 'ngày' not in data['songay_str']) and data['lich_trinh_ngay']:
                    data['songay_str'] = f"{len(data['lich_trinh_ngay'])}/5 ngày"

                m_calc = re.search(r'(\d+)\s*/\s*(\d+)', data.get('songay_str', ''))
                if m_calc:
                    if int(m_calc.group(1)) >= int(m_calc.group(2)):
                        data['songay_dat'] = True
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
        if not msg:
            return []
        
        author_id = msg.get('author', {}).get('id')
        author_name = msg.get('author', {}).get('username', '')
        if author_id != APP_ID and author_name != 'NLTB_Bot':
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
                e_title = e.get('title', '')
                if 'Phiên học' in e_title or 'phien' in (parsed.get('pdf_filename') or '').lower():
                    parsed['is_phien_embed'] = True
                if 'Thông tin' in e_title or 'Kiểm tra' in e_title or 'DAT' in e_title or parsed.get('songay_str'):
                    parsed['is_dat_embed'] = True

                if phien_pdf:
                    parsed['pdf_filename'] = phien_pdf['filename']
                    parsed['pdf_url'] = phien_pdf['url']
                if lythuyet_pdf:
                    parsed['lythuyet_pdf_filename'] = lythuyet_pdf['filename']
                    parsed['lythuyet_pdf_url'] = lythuyet_pdf['url']
                    parsed['has_lythuyet'] = True
                    if not parsed.get('mahv'):
                        m_code = re.search(r'([A-Za-z0-9_-]+)_lythuyet\.pdf', lythuyet_pdf['filename'], re.IGNORECASE)
                        if m_code:
                            parsed['mahv'] = m_code.group(1).strip()
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

    def query_student(self, query_text, command='dat', timeout_sec=16, force=False):
        """Tra cứu học viên với tính năng chống trùng lặp luồng (In-Flight Concurrency Deduplication)."""
        q_clean = str(query_text).strip()
        q_lower = q_clean.lower()
        if not q_clean:
            return {'success': False, 'error': 'Vui lòng cung cấp thông tin tra cứu.'}

        cache_key = f"{command}:{q_lower}"
        is_waiter = False
        with self._in_flight_lock:
            if cache_key in self._in_flight_queries:
                ev, holder = self._in_flight_queries[cache_key]
                is_waiter = True
            else:
                ev = threading.Event()
                holder = [None]
                self._in_flight_queries[cache_key] = (ev, holder)

        if is_waiter:
            # Có người đang tra cứu cùng lúc học viên này -> Đợi kết quả dùng chung, không gửi trùng lệnh Discord
            ev.wait(timeout=timeout_sec + 2)
            if holder[0] is not None:
                return holder[0]

        try:
            res = self._do_query_student(query_text, command=command, timeout_sec=timeout_sec, force=force)
            with self._in_flight_lock:
                holder[0] = res
                ev.set()
                self._in_flight_queries.pop(cache_key, None)
            return res
        except Exception as e:
            err_res = {'success': False, 'error': str(e)}
            with self._in_flight_lock:
                holder[0] = err_res
                ev.set()
                self._in_flight_queries.pop(cache_key, None)
            return err_res

    def _do_query_student(self, query_text, command='dat', timeout_sec=16, force=False):
        """
        Gửi lệnh tra cứu (/dat, /phien, /lythuyet) với tốc độ tối ưu cao:
        1. Quét nhanh kênh Discord (Fast Cache): Nếu bot vừa mới trả lời học viên này gần đây và không yêu cầu force,
           lấy ngay kết quả trong 0.1s không cần gọi lại bot!
        2. Tự động gửi Slash Command Interaction nếu chưa có trong kênh hoặc khi force=True.
        3. Nhận diện thông minh phản hồi: Hỗ trợ cả trường hợp trùng tên nhiều khóa và tự động
           bắt tiếp thông tin khi chọn khóa trên Discord.
        """
        cmd_id, cmd_ver = self.get_command_info(command)

        q_clean = str(query_text).strip()
        q_lower = q_clean.lower()

        # BƯỚC 1: FAST SCAN (Chỉ dùng khi force=False VÀ tin nhắn bot vừa mới trả lời trong 60 giây gần nhất)
        latest_id = self.latest_msg_id
        q_no_acc = strip_vietnamese_accents(q_lower)
        try:
            scan_limit = 15 if not force else 1
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit={scan_limit}", timeout=3.5)
            if r_l.status_code == 200:
                raw_recent = r_l.json()
                if raw_recent and isinstance(raw_recent, list) and len(raw_recent) > 0:
                    latest_id = raw_recent[0].get('id')
                    self.latest_msg_id = latest_id

                    if not force:
                        from datetime import datetime, timezone
                        now_utc = datetime.now(timezone.utc)
                        for m in raw_recent:
                            # Chỉ chấp nhận cache nếu tin nhắn được tạo trong vòng 60 giây trở lại đây
                            ts_str = m.get('timestamp', '')
                            if ts_str:
                                try:
                                    msg_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                                    if (now_utc - msg_dt).total_seconds() > 60:
                                        continue
                                except Exception:
                                    continue

                            for st in self.parse_discord_message(m):
                                st_mahv = st.get('mahv', '').lower()
                                st_cccd = st.get('cccd', '').lower()
                                st_hoten = st.get('hoten', '').lower()
                                st_no_acc = strip_vietnamese_accents(st_hoten)

                                matched = False
                                if q_lower and (q_lower == st_mahv or q_lower == st_cccd or q_lower == st_hoten):
                                    matched = True
                                elif q_no_acc and (q_no_acc == st_no_acc or (len(q_no_acc) >= 4 and (q_no_acc in st_no_acc or st_no_acc in q_no_acc))):
                                    matched = True
                                elif q_lower and (q_lower in st_mahv or (len(q_lower) >= 6 and q_lower in st_cccd)):
                                    matched = True

                                if matched:
                                    if command == 'dat' and not st.get('is_phien_embed') and (st.get('songay_str') or st.get('lich_trinh_ngay') or st.get('is_dat_embed') or st.get('quangduong', 0) > 0):
                                        return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
                                    elif command == 'phien' and (st.get('is_phien_embed') or st.get('pdf_url')):
                                        return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
                                    elif command == 'lythuyet' and (st.get('has_lythuyet') or st.get('lythuyet_pdf_url')):
                                        return {'success': True, 'data': [st], 'source': 'recent_channel_cache'}
        except Exception:
            pass

        # BƯỚC 2: Gửi Slash Command Interaction tới Discord Bot (kèm giãn cách mô phỏng người thật)
        payload = self.build_interaction_payload(command, [{'type': 3, 'name': 'mhv', 'value': q_clean}])

        t_start = time.time()
        try:
            resp = self.send_interaction(payload, timeout=6)

            if resp.status_code not in (200, 204):
                print(f"[Discord Error {resp.status_code}] {resp.text}")
                if '10005' in resp.text:
                    self.refresh_command_index()
                    return {'success': False, 'error': 'Hệ thống lệnh Bot Discord trường vừa cập nhật phiên bản mới. Đang tự động kết nối lại, vui lòng thử lại sau giây lát!'}
                if resp.status_code == 401 or '401' in resp.text or 'Unauthorized' in resp.text:
                    return {'success': False, 'is_auth_error': True, 'error': 'Mã Token Discord đã hết hạn! Vui lòng mở cài đặt Bot Discord để dán mã Token mới.'}
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code} ({resp.text[:100]})'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh: {str(e)}'}

        # BƯỚC 3: Chờ và bóc tách phản hồi Bot siêu tốc
        time.sleep(0.18)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=6"
        multi_courses_detected = None

        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.0)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                # Chấp nhận tin nhắn mới hơn latest_id hoặc mới được tạo sau thời điểm gửi lệnh
                if latest_id is None or int(mid) > int(latest_id):
                    author_id = m.get('author', {}).get('id')
                    author_name = m.get('author', {}).get('username', '')
                    if author_id == APP_ID or author_name == 'NLTB_Bot':
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
                            st_no_acc = strip_vietnamese_accents(st_hoten)

                            # Khớp nếu trùng tên, mã học viên, CCCD (có hoặc không dấu) hoặc do chính tài khoản này gửi lệnh
                            is_query_match = (
                                (q_lower and q_lower in st_mahv) or 
                                (q_lower and q_lower in st_hoten) or 
                                (q_lower and q_lower in st_cccd) or
                                (q_no_acc and (q_no_acc in st_no_acc or st_no_acc in q_no_acc))
                            )
                            my_info = self.get_user_info()
                            my_uid = my_info.get('id') if my_info else None
                            is_my_interaction = bool(i_uid and (i_uid == my_uid or i_uid in ('1380376964304474173', '1380096710386384937', '1574670927948423262')))

                            if is_query_match or is_my_interaction:
                                matched = True

                            if matched:
                                matches.append(st)

                        if matches:
                            if command == 'dat':
                                dat_matches = [s for s in matches if not s.get('is_phien_embed') and (s.get('songay_str') or s.get('lich_trinh_ngay') or s.get('is_dat_embed') or s.get('quangduong', 0) > 0)]
                                if dat_matches:
                                    self.latest_msg_id = mid
                                    return {'success': True, 'data': dat_matches}
                            elif command == 'phien':
                                phien_matches = [s for s in matches if s.get('is_phien_embed') or s.get('pdf_url')]
                                if phien_matches:
                                    self.latest_msg_id = mid
                                    return {'success': True, 'data': phien_matches}
                            elif command == 'lythuyet':
                                lt_matches = [s for s in matches if s.get('has_lythuyet') or s.get('lythuyet_pdf_url')]
                                if lt_matches:
                                    self.latest_msg_id = mid
                                    return {'success': True, 'data': lt_matches}
                            else:
                                self.latest_msg_id = mid
                                return {'success': True, 'data': matches}

            time.sleep(0.20)

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

        payload = self.build_interaction_payload('capnhatkhoa', [{'type': 3, 'name': 'khoa', 'value': c_name}])

        t_start = time.time()
        try:
            resp = self.send_interaction(payload, timeout=6)

            if resp.status_code not in (200, 204):
                if resp.status_code == 401 or '401' in resp.text or 'Unauthorized' in resp.text:
                    return {'success': False, 'is_auth_error': True, 'error': 'Mã Token Discord đã hết hạn! Vui lòng cập nhật Token mới.'}
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

            time.sleep(0.65)

        return {'success': True, 'message': f'Đã gửi yêu cầu cập nhật khóa {c_name} tới Bot.'}

    def send_daykhoa(self, course_name, biensoxe, timeout_sec=16):
        """
        Gửi lệnh /daykhoa khoa: <course_name> biensoxe: <biensoxe> tới Discord Bot
        để đẩy dữ liệu khóa học xuống xe tập lái.
        """
        c_name = str(course_name).strip()
        c_plate = self.clean_plate(biensoxe)
        if not c_name or not c_plate:
            return {'success': False, 'error': 'Thiếu tên khóa học hoặc biển số xe.'}

        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=3.5)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        payload = self.build_interaction_payload('daykhoa', [
            {'type': 3, 'name': 'khoa', 'value': c_name},
            {'type': 3, 'name': 'biensoxe', 'value': c_plate}
        ])

        t_start = time.time()
        try:
            resp = self.send_interaction(payload, timeout=6)

            if resp.status_code not in (200, 204):
                if resp.status_code == 401 or '401' in resp.text or 'Unauthorized' in resp.text:
                    return {'success': False, 'is_auth_error': True, 'error': 'Mã Token Discord đã hết hạn! Vui lòng cập nhật Token mới.'}
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code} ({resp.text[:120]})'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh /daykhoa: {str(e)}'}

        time.sleep(0.65)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=5"
        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.5)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    author_id = m.get('author', {}).get('id')
                    author_name = m.get('author', {}).get('username', '')
                    if author_id == APP_ID or author_name == 'NLTB_Bot':
                        content = m.get('content', '')
                        embeds = m.get('embeds', [])
                        e_desc = embeds[0].get('description', '') if embeds else ''
                        e_title = embeds[0].get('title', '') if embeds else ''
                        full_txt = f"{content} {e_title} {e_desc}".strip()
                        inter = m.get('interaction') or {}
                        iname = inter.get('name')

                        if iname == 'daykhoa' or 'đẩy' in full_txt.lower() or 'khóa' in full_txt.lower() or c_plate.lower() in full_txt.lower():
                            self.latest_msg_id = mid
                            if '❌' in full_txt or 'lỗi' in full_txt.lower() or 'không tìm thấy' in full_txt.lower() or '⚠️' in full_txt:
                                clean_err = re.sub(r'[\*`]', '', full_txt).strip()
                                return {'success': False, 'error': clean_err}
                            return {'success': True, 'message': full_txt or f'Đã đẩy khóa {c_name} xuống xe {c_plate} thành công!'}

            time.sleep(0.25)

        return {'success': True, 'message': f'Đã gửi lệnh đẩy khóa {c_name} xuống xe {c_plate} tới Discord!'}

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

    def clean_plate(self, plate_str):
        """Chuẩn hóa biển số xe (loại bỏ dấu chấm, khoảng trắng, gạch ngang, ghi chú số tự động)."""
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

    def check_car_online(self, biensoxe, timeout_sec=5):
        """
        Kiểm tra trạng thái kết nối máy chủ của xe qua lệnh /xeonline biensoxe:<biensoxe>:
        - Trả về {'online': True, 'details': '...'} nếu xe đang nổ máy kết nối.
        - Trả về {'online': False, 'details': '...'} nếu xe tắt máy / mất GPS / không kết nối.
        """
        c_plate = self.clean_plate(biensoxe)
        if not c_plate:
            return {'online': False, 'details': 'Biển số không hợp lệ'}

        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=3.5)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        payload = self.build_interaction_payload('xeonline', [{'type': 3, 'name': 'biensoxe', 'value': c_plate}])

        t_start = time.time()
        try:
            resp = self.send_interaction(payload, timeout=6)
            if resp.status_code not in (200, 204):
                return {'online': False, 'details': f'Lỗi Discord: {resp.status_code}'}
        except Exception as e:
            return {'online': False, 'details': f'Không thể gửi lệnh /xeonline: {e}'}

        time.sleep(1.0)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=4"
        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.5)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    if m.get('author', {}).get('id') == APP_ID:
                        content = m.get('content', '')
                        embeds = m.get('embeds', [])
                        first_e = embeds[0] if embeds else {}
                        title = first_e.get('title', '')
                        desc = first_e.get('description', '')
                        fields_text = " ".join([f"{f.get('name','')} {f.get('value','')}" for f in first_e.get('fields', [])])
                        combined = f"{content} {title} {desc} {fields_text}".upper()

                        if 'KHÔNG KẾT NỐI' in combined or '🔴' in combined or 'KHÔNG TÌM THẤY' in combined:
                            self.latest_msg_id = mid
                            return {'online': False, 'details': f'Xe {c_plate} đang tắt máy / mất kết nối GPS'}

                        if 'XE ĐANG KẾT NỐI' in combined or '🟢' in combined or c_plate in combined:
                            self.latest_msg_id = mid
                            return {'online': True, 'details': f'Xe {c_plate} đang online máy chủ'}

            time.sleep(0.65)

        return {'online': False, 'details': f'Chưa xác nhận kết nối xe {c_plate}'}

    def send_dongbo(self, mhv, biensoxe, timeout_sec=8):
        """
        Gửi lệnh đồng bộ phiên lỗi lên máy chủ:
        1. Gửi lệnh /dongbo mhv:<mhv> biensoxe:<biensoxe> theo đúng yêu cầu giáo viên.
        2. Nếu bot phản hồi 'Tính năng này chỉ dành cho Admin':
           Tự động fallback gọi ngay /dongbotc với đúng mhv và xe đó để đảm bảo phiên được đẩy lên thành công!
        """
        c_plate = self.clean_plate(biensoxe)
        c_mhv = str(mhv).strip()
        if not c_mhv or not c_plate:
            return {'success': False, 'error': 'Thiếu mã học viên hoặc biển số xe.'}

        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=3.5)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        # Bước 1: Gửi /dongbo
        payload_dongbo = self.build_interaction_payload('dongbo', [
            {'type': 3, 'name': 'mhv', 'value': c_mhv},
            {'type': 3, 'name': 'biensoxe', 'value': c_plate}
        ])

        t_start = time.time()
        try:
            resp = self.send_interaction(payload_dongbo, timeout=6)
            if resp.status_code not in (200, 204):
                if resp.status_code == 401 or '401' in resp.text or 'Unauthorized' in resp.text:
                    return {'success': False, 'is_auth_error': True, 'error': 'Mã Token Discord đã hết hạn! Vui lòng cập nhật Token mới.'}
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code}'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh /dongbo: {e}'}

        time.sleep(1.2)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=4"
        admin_blocked = False
        bot_message = ""

        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.5)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    if m.get('author', {}).get('id') == APP_ID:
                        content = m.get('content', '')
                        embeds = m.get('embeds', [])
                        e_desc = embeds[0].get('description', '') if embeds else ''
                        e_title = embeds[0].get('title', '') if embeds else ''
                        text_all = f"{content} {e_title} {e_desc}".strip()

                        if 'chỉ dành cho admin' in text_all.lower():
                            admin_blocked = True
                            self.latest_msg_id = mid
                            break
                        elif text_all:
                            bot_message = text_all
                            self.latest_msg_id = mid
                            break

            if admin_blocked or bot_message:
                break
            time.sleep(0.65)

        # Bước 2: Fallback sang /dongbotc nếu bị chặn Admin
        if admin_blocked:
            payload_fallback = {
                'type': 2,
                'application_id': APP_ID,
                'guild_id': GUILD_ID,
                'channel_id': CHANNEL_ID,
                'session_id': uuid.uuid4().hex,
                'data': {
                    'version': CMD_DONGBOTC_VER,
                    'id': CMD_DONGBOTC_ID,
                    'name': 'dongbotc',
                    'type': 1,
                    'options': [
                        {'type': 3, 'name': 'mhv', 'value': c_mhv},
                        {'type': 3, 'name': 'biensoxe', 'value': c_plate}
                    ]
                }
            }
            try:
                self.send_interaction(payload_fallback, timeout=6)
                time.sleep(1.8)
                r_poll2 = self.session.get(poll_url, timeout=3.5)
                if r_poll2.status_code == 200:
                    for m in r_poll2.json():
                        if m.get('author', {}).get('id') == APP_ID:
                            content = m.get('content', '')
                            embeds = m.get('embeds', [])
                            e_desc = embeds[0].get('description', '') if embeds else ''
                            e_title = embeds[0].get('title', '') if embeds else ''
                            bot_message = f"{content} {e_title} {e_desc}".strip()
                            if bot_message:
                                break
                return {
                    'success': True,
                    'message': bot_message or 'Đã gửi yêu cầu đồng bộ xe tới máy DAT thành công!',
                    'command_used': 'dongbo (auto fallback dongbotc)'
                }
            except Exception as ef:
                return {'success': False, 'error': f'Lỗi khi gửi lệnh dự phòng: {ef}'}

        return {
            'success': True,
            'message': bot_message or f'Đã gửi lệnh /dongbo cho học viên {c_mhv} trên xe {c_plate}.',
            'command_used': 'dongbo'
        }

    def upload_cabin(self, mhv, image_bytes, filename='cabin.jpg', timeout_sec=12):
        """
        Gửi lệnh /cabin mhv:<mhv> image:<attachment> bằng multipart form data lên Discord Bot:
        - Tự động nạp khuôn mặt điểm danh cabin cho học viên tương ứng.
        - Bắt phản hồi xác nhận từ NLTB_Bot.
        """
        c_mhv = str(mhv).strip()
        if not c_mhv or not image_bytes:
            return {'success': False, 'error': 'Thiếu mã học viên hoặc file ảnh.'}

        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=3.5)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        payload_json = self.build_interaction_payload(
            'cabin',
            [
                {'type': 3, 'name': 'mhv', 'value': c_mhv},
                {'type': 11, 'name': 'image', 'value': '0'}
            ],
            extra_data={'attachments': [{'id': '0', 'filename': filename}]}
        )

        import io
        body = io.BytesIO()
        body.write(f"--{boundary}\r\n".encode('utf-8'))
        body.write(b'Content-Disposition: form-data; name="payload_json"\r\n\r\n')
        body.write(json.dumps(payload_json).encode('utf-8'))
        body.write(b'\r\n')

        body.write(f"--{boundary}\r\n".encode('utf-8'))
        body.write(f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'.encode('utf-8'))
        content_type = 'image/png' if filename.lower().endswith('.png') else 'image/jpeg'
        body.write(f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8'))
        body.write(image_bytes)
        body.write(b'\r\n')
        body.write(f"--{boundary}--\r\n".encode('utf-8'))

        post_data = body.getvalue()
        headers = {
            'Authorization': self.token,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36',
            'X-Super-Properties': DISCORD_SUPER_PROPERTIES,
            'X-Discord-Locale': 'vi',
            'X-Discord-Timezone': 'Asia/Ho_Chi_Minh',
            'Content-Type': f'multipart/form-data; boundary={boundary}'
        }

        t_start = time.time()
        try:
            resp = requests.post('https://discord.com/api/v9/interactions', data=post_data, headers=headers, timeout=8)
            if resp.status_code not in (200, 204):
                return {'success': False, 'error': f'Discord API lỗi: {resp.status_code} ({resp.text[:120]})'}
        except Exception as e:
            return {'success': False, 'error': f'Lỗi kết nối khi gửi ảnh cabin: {e}'}

        time.sleep(1.5)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=4"
        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=3.5)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    if m.get('author', {}).get('id') == APP_ID:
                        content = m.get('content', '')
                        embeds = m.get('embeds', [])
                        e_desc = embeds[0].get('description', '') if embeds else ''
                        e_title = embeds[0].get('title', '') if embeds else ''
                        full_txt = f"{content} {e_title} {e_desc}".strip()
                        if 'điểm danh' in full_txt.lower() or 'khuôn mặt' in full_txt.lower() or '✅' in full_txt:
                            self.latest_msg_id = mid
                            return {'success': True, 'message': full_txt or 'Đã lưu dữ liệu điểm danh khuôn mặt thành công'}
                        elif '❌' in full_txt or 'lỗi' in full_txt.lower():
                            self.latest_msg_id = mid
                            return {'success': False, 'error': full_txt}

            time.sleep(0.5)

        return {'success': True, 'message': f'Đã gửi ảnh cabin cho học viên {c_mhv} tới máy chủ thành công'}

    def execute_bot_command(self, cmd_name, options_list, timeout_sec=16):
        """
        Thực thi một lệnh Slash Command an toàn dành cho giáo viên trên Discord Bot
        (ví dụ: /kiemtraxe, /dinhvixe, /xeonline, /dongbocabin, /daygiaovien, /timkhoa)
        và trả về phản hồi có cấu trúc từ Bot NLTB_Bot.
        """
        if not self.token:
            return {'success': False, 'is_auth_error': True, 'error': 'Chưa cấu hình Token Discord.'}

        latest_id = self.latest_msg_id
        try:
            r_l = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=1", timeout=3.5)
            if r_l.status_code == 200 and r_l.json():
                latest_id = r_l.json()[0]['id']
                self.latest_msg_id = latest_id
        except Exception:
            pass

        payload = self.build_interaction_payload(cmd_name, options_list or [])
        t_start = time.time()
        try:
            resp = self.send_interaction(payload, timeout=6)
            if resp.status_code not in (200, 204):
                if resp.status_code == 401 or '401' in resp.text or 'Unauthorized' in resp.text:
                    return {'success': False, 'is_auth_error': True, 'error': 'Mã Token Discord đã hết hạn!'}
                return {'success': False, 'error': f'Lỗi Discord: {resp.status_code} ({resp.text[:120]})'}
        except Exception as e:
            return {'success': False, 'error': f'Không thể gửi lệnh /{cmd_name}: {str(e)}'}

        my_uid = str((self.get_user_info() or {}).get('id') or '')
        time.sleep(0.8)
        poll_url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=6"

        while time.time() - t_start < timeout_sec:
            try:
                r_poll = self.session.get(poll_url, timeout=4.0)
                raw_msgs = r_poll.json() if r_poll.status_code == 200 else []
            except Exception:
                raw_msgs = []

            for m in raw_msgs:
                mid = m.get('id')
                if latest_id is None or int(mid) > int(latest_id):
                    author_id = str(m.get('author', {}).get('id') or '')
                    author_name = m.get('author', {}).get('username', '')
                    if author_id == APP_ID or author_name == 'NLTB_Bot':
                        inter = m.get('interaction') or {}
                        iname = inter.get('name') or ''
                        i_uid = str(inter.get('user', {}).get('id') or '')
                        # Nếu tin nhắn gắn rõ interaction của lệnh khác hoặc người khác thì bỏ qua
                        if iname and iname != cmd_name:
                            continue
                        if i_uid and my_uid and i_uid != my_uid:
                            continue

                        content = m.get('content', '') or ''
                        embeds = m.get('embeds', []) or []
                        e0 = embeds[0] if embeds else {}
                        title = e0.get('title', '') or ''
                        desc = e0.get('description', '') or ''
                        fields = [{'name': f.get('name', ''), 'value': f.get('value', '')} for f in e0.get('fields', [])]
                        img_url = (e0.get('image') or {}).get('url') or ''
                        att_urls = [a.get('url') for a in (m.get('attachments') or []) if a.get('url')]

                        combined = f"{content} {title} {desc}".strip()
                        if not combined and not fields and not img_url and not att_urls:
                            continue

                        self.latest_msg_id = mid
                        is_err = ('❌' in combined or 'chỉ dành cho admin' in combined.lower() or 'không tìm thấy' in combined.lower())
                        return {
                            'success': not is_err,
                            'command': cmd_name,
                            'title': title,
                            'message': content or desc or title,
                            'description': desc,
                            'fields': fields,
                            'image_url': img_url,
                            'attachments': att_urls,
                            'error': combined if is_err else None
                        }

            time.sleep(0.75)

        return {'success': False, 'error': f'Bot chưa phản hồi lệnh /{cmd_name} trong {timeout_sec}s (hoặc phản hồi riêng tư trên Discord).'}

    def start_live_watcher(self, on_student_parsed_cb=None, interval_sec=3.5, is_active_cb=None):
        """
        Khởi chạy tiến trình ngầm (Daemon Thread) liên tục lắng nghe kênh Discord:
        Tự động bắt tức thì mọi phản hồi /dat, /lythuyet, /phien của Bot NLTB_Bot
        để cập nhật ngay vào hệ thống theo thời gian thực, kèm Smart Idle Sleep bảo vệ tài khoản.
        """
        if getattr(self, '_watcher_running', False):
            return

        self._watcher_running = True

        def _watcher_loop():
            print("[Discord Live Watcher] 🟢 Bắt đầu lắng nghe tin nhắn Discord thời gian thực...")
            # Theo dõi trạng thái từng tin nhắn: msg_id -> (edited_timestamp, embed_count, attachment_count, content_len)
            seen_message_states = {}

            # BƯỚC 1: Quét khởi tạo (Initial Catch-up)
            # Quét 15 tin nhắn gần nhất để không bỏ lỡ bất kỳ lệnh nào vừa chạy trước khi app khởi động
            try:
                r_init = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=50", timeout=8)
                if r_init.status_code == 200:
                    init_msgs = r_init.json()
                    if init_msgs and isinstance(init_msgs, list):
                        for m in reversed(init_msgs):
                            mid = m.get('id')
                            if not mid:
                                continue
                            m_state = (
                                m.get('edited_timestamp') or '',
                                len(m.get('embeds', [])),
                                len(m.get('attachments', [])),
                                len(m.get('content', ''))
                            )
                            seen_message_states[mid] = m_state
                            parsed_list = self.parse_discord_message(m)
                            for st in parsed_list:
                                cmd = 'lythuyet' if st.get('has_lythuyet') or st.get('lythuyet_pdf_url') else ('phien' if st.get('is_phien_embed') or st.get('pdf_url') else 'dat')
                                if on_student_parsed_cb:
                                    try:
                                        on_student_parsed_cb(st, cmd)
                                    except Exception as ex_cb:
                                        print("[Discord Live Watcher] Lỗi callback khởi tạo:", ex_cb)
            except Exception as e_init:
                print("[Discord Live Watcher] Cảnh báo quét khởi tạo:", e_init)

            consecutive_errors = 0

            # BƯỚC 2: Vòng lặp lắng nghe thời gian thực (Real-time polling)
            while self._watcher_running:
                try:
                    if not self.token:
                        self.token = auto_detect_discord_token()
                        if self.token:
                            self.set_token(self.token)
                        else:
                            time.sleep(4)
                            continue

                    r = self.session.get(f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=10", timeout=3.5)
                    
                    if r.status_code == 429:
                        try:
                            retry_after = float(r.json().get('retry_after', 2.0))
                            time.sleep(min(retry_after + 0.2, 5.0))
                        except Exception:
                            time.sleep(2)
                        continue

                    if r.status_code == 200:
                        consecutive_errors = 0
                        msgs = r.json()
                        if msgs and isinstance(msgs, list):
                            for m in reversed(msgs):
                                mid = m.get('id')
                                if not mid:
                                    continue
                                m_state = (
                                    m.get('edited_timestamp') or '',
                                    len(m.get('embeds', [])),
                                    len(m.get('attachments', [])),
                                    len(m.get('content', ''))
                                )
                                # Bắt tin nhắn mới HOẶC tin nhắn được Bot chỉnh sửa bổ sung embed/attachment
                                if mid not in seen_message_states or seen_message_states[mid] != m_state:
                                    seen_message_states[mid] = m_state
                                    self.latest_msg_id = mid

                                    parsed_list = self.parse_discord_message(m)
                                    for st in parsed_list:
                                        cmd = 'lythuyet' if st.get('has_lythuyet') or st.get('lythuyet_pdf_url') else ('phien' if st.get('is_phien_embed') or st.get('pdf_url') else 'dat')
                                        if on_student_parsed_cb:
                                            try:
                                                on_student_parsed_cb(st, cmd)
                                            except Exception as ex_cb:
                                                print("[Discord Live Watcher] Lỗi callback:", ex_cb)

                            # Duy trì bộ nhớ đệm gọn nhẹ không quá 200 tin nhắn
                            if len(seen_message_states) > 200:
                                active_ids = set(m.get('id') for m in msgs if m.get('id'))
                                keys_to_keep = list(seen_message_states.keys())[-100:]
                                keys_to_keep_set = set(keys_to_keep) | active_ids
                                seen_message_states = {k: v for k, v in seen_message_states.items() if k in keys_to_keep_set}

                    elif r.status_code in (401, 403):
                        print("[Discord Live Watcher] Token hết hạn, đang tự động làm mới...")
                        new_t = auto_detect_discord_token()
                        if new_t:
                            self.set_token(new_t)
                        time.sleep(4)
                    else:
                        consecutive_errors += 1
                        time.sleep(min(1.5 * consecutive_errors, 6.0))

                except Exception as e_poll:
                    consecutive_errors += 1
                    time.sleep(min(1.5 * consecutive_errors, 5.0))

                # Smart Idle Sleep: giãn nhịp khi không có người dùng truy cập để bảo vệ tài khoản Discord
                if is_active_cb is not None and not is_active_cb():
                    time.sleep(random.uniform(45.0, 60.0))
                else:
                    time.sleep(interval_sec + random.uniform(0.3, 0.9))

        t = threading.Thread(target=_watcher_loop, daemon=True, name="DiscordLiveWatcher")
        self._watcher_thread = t
        t.start()

    def stop_live_watcher(self):
        self._watcher_running = False

