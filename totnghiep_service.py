"""
totnghiep_service.py - Module đánh giá điều kiện thi tốt nghiệp học viên
Dựa trên việc tự động phân tích file PDF Báo Cáo Lý Thuyết từ Discord Bot (NLTB_Bot).

Quy tắc xét điều kiện thi tốt nghiệp (theo yêu cầu của thầy):
- Tự động chạy /dat rồi lấy mã học viên chạy tiếp /lythuyet.
- Nếu TẤT CẢ các cột/môn đều TÍCH XANH, TRỪ CỘT HÌNH CABIN (Nhận diện Cabin) thì ĐẠT.
- Còn lại nếu có bất kỳ dấu X nào thì KHÔNG (Không Đạt).
"""

import os
import re
try:
    import fitz
except ImportError:
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None

# 13 Hàng kiểm tra trong bảng báo cáo lý thuyết của Discord Bot
ROW_DEFINITIONS = [
    ('phap_luat', 'Pháp luật TT14', 321, False),
    ('ktlx', 'Kỹ thuật lái xe TT14', 351, False),
    ('cau_tao', 'Cấu tạo TT14', 381, False),
    ('dao_duc', 'Đạo đức + PCCC TT14', 411, False),
    ('mo_phong', 'Mô phỏng (LT)', 441, False),
    ('note_cabin', 'Note Cabin (Bài cabin)', 471, False),
    ('gio_cabin', 'Giờ Cabin', 501, False),
    ('da_nop_sk', 'Đã nộp SK', 531, False),
    ('da_nop_hd', 'Đã nộp HĐ', 561, False),
    ('gio_cao_toc', 'Giờ Cao Tốc', 591, False),
    ('km_cao_toc', 'Km Cao Tốc', 621, False),
    ('van_toc_tb', 'Vận tốc TB', 651, False),
    ('hinh_cabin', 'Nhận diện Cabin (Hình cabin)', 681, True)  # Luôn miễn trừ theo yêu cầu của thầy
]

def extract_car_from_lythuyet_pdf(pdf_path):
    """
    Trích xuất biển số xe được phân trong file PDF báo cáo lý thuyết (_lythuyet.pdf).
    Tìm dòng 'Phân Xe: <biển số xe>' (VD: 81A78293, 77A24156).
    """
    if not pdf_path or not os.path.exists(pdf_path):
        return None
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text = page.get_text()
            m = re.search(r'Phân\s*Xe:\s*([^\n\r]+)', text, re.IGNORECASE)
            if not m:
                m = re.search(r'Biển\s*số\s*xe:\s*([^\n\r]+)', text, re.IGNORECASE)
            if m:
                raw_val = m.group(1).strip()
                clean = re.sub(r'[^A-Za-z0-9]', '', raw_val).upper()
                if clean:
                    return clean
    except Exception as e:
        print("Lỗi trích xuất xe từ PDF lý thuyết:", e)
    return None

def evaluate_pdf_file(pdf_path, exempt_all_cabin=False):
    """
    Phân tích file PDF Báo cáo lý thuyết:
    Quét vị trí tọa độ các hình ảnh Tích Xanh (cột Hoàn thành) và Dấu X (cột Không H/t)
    để đánh giá chính xác từng chỉ tiêu.
    """
    if not os.path.exists(pdf_path):
        return {
            'ketqua_totnghiep': 'Chưa tra LT',
            'is_dat': False,
            'failing_items': ['Chưa có file PDF'],
            'passed_items': [],
            'details': {}
        }

    if fitz is None:
        return {
            'ketqua_totnghiep': 'Chưa tra LT',
            'is_dat': False,
            'failing_items': ['Thư viện PDF chưa sẵn sàng'],
            'passed_items': [],
            'details': {}
        }

    try:
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            return {
                'ketqua_totnghiep': 'Chưa tra LT',
                'is_dat': False,
                'failing_items': ['File PDF rỗng'],
                'passed_items': [],
                'details': {}
            }

        page = doc[0]
        img_list = page.get_images()
        checks = []
        crosses = []

        for info in img_list:
            xref = info[0]
            rects = page.get_image_rects(xref)
            for r in rects:
                # Cột Hoàn thành (Tích xanh): x trong khoảng [250, 325]
                if 250 <= r.x0 <= 325 and r.y0 > 250:
                    checks.append(r.y0)
                # Cột Không H/t (Dấu X đỏ): x trong khoảng [325, 400]
                elif 325 <= r.x0 <= 400 and r.y0 > 250:
                    crosses.append(r.y0)

        details = {}
        failing_items = []
        passed_items = []

        for key, name, y_pos, is_exempt_default in ROW_DEFINITIONS:
            # Nếu exempt_all_cabin = True thì miễn trừ cả Note Cabin và Giờ Cabin
            is_exempt = is_exempt_default or (exempt_all_cabin and 'cabin' in key)

            is_pass = any(abs(c - y_pos) < 15 for c in checks)
            is_fail = any(abs(x - y_pos) < 15 for x in crosses)

            if is_pass and not is_fail:
                status = 'PASS'
                passed_items.append(name)
            elif is_fail:
                status = 'FAIL'
                if not is_exempt:
                    failing_items.append(name)
            else:
                status = 'NONE'
                # Nếu không có tích xanh ở môn bắt buộc thì tính là chưa đạt
                if not is_exempt:
                    failing_items.append(name)

            details[key] = {
                'name': name,
                'status': status,
                'exempt': is_exempt
            }

        # Đánh giá tổng: Nếu tất cả các cột đều tích xanh (trừ cột hình cabin) thì ĐẠT
        is_dat = (len(failing_items) == 0)

        return {
            'ketqua_totnghiep': 'Đạt' if is_dat else 'Không',
            'is_dat': is_dat,
            'failing_items': failing_items,
            'passed_items': passed_items,
            'details': details
        }
    except Exception as e:
        print(f"Lỗi phân tích PDF tốt nghiệp ({pdf_path}):", e)
        return {
            'ketqua_totnghiep': 'Lỗi PDF',
            'is_dat': False,
            'failing_items': [f"Lỗi đọc file: {str(e)}"],
            'passed_items': [],
            'details': {}
        }

def evaluate_student(student_record, downloads_dir, exempt_all_cabin=False):
    """
    Đánh giá điều kiện thi tốt nghiệp toàn diện cho một bản ghi học viên.
    Bao gồm cả 2 nhóm điều kiện bắt buộc:
    1. Nhóm thực hành DAT: Số Km thực hành, Số giờ thực hành (gồm đêm, tự động), và Số ngày đường.
    2. Nhóm lý thuyết & hồ sơ: 13 mục từ báo cáo của bot Discord (Tất cả tích xanh, miễn trừ hình cabin).
    """
    if not student_record:
        return student_record

    # 1. Đánh giá nhóm thực hành DAT (Số Km, Số Giờ, Số Ngày Đường)
    curr_km = float(student_record.get('quangduong', 0.0) or 0.0)
    target_km = float(student_record.get('target_km', 810.0) or 810.0)
    km_ok = bool(student_record.get('quangduong_dat', False))
    km_thieu = float(student_record.get('quangduong_thieu', 0.0) or 0.0)

    curr_gio = float(student_record.get('thoigian', 0.0) or 0.0)
    target_gio = float(student_record.get('target_gio', 20.0) or 20.0)
    gio_ok = bool(student_record.get('thoigian_dat', False))
    dem_ok = bool(student_record.get('thoigian_dem_dat', False))
    target_td_raw = student_record.get('thoigian_tudong_toithieu')
    target_td = float(target_td_raw) if target_td_raw is not None else 2.0
    tudong_ok = bool(student_record.get('thoigian_tudong_dat', False)) or (target_td == 0.0)
    all_time_ok = gio_ok and dem_ok and tudong_ok

    curr_dem = float(student_record.get('thoigian_dem', 0.0) or 0.0)
    target_dem = float(student_record.get('thoigian_dem_toithieu', 2.0) or 2.0)
    curr_td = float(student_record.get('thoigian_tudong', 0.0) or 0.0)

    songay_str = student_record.get('songay_str', '0/5 ngày')
    songay_ok = bool(student_record.get('songay_dat', False))

    combined_details = {}
    combined_failing = []

    # Mục DAT 1: Số Km thực hành
    km_info = f"Đã hoàn thành {curr_km:.1f} / {target_km:.0f} km" if km_ok else (
        f"Thiếu {km_thieu:.1f} km" if curr_km > 0 else f"Chưa hoàn thành số km (0.0 / {target_km:.0f} km)"
    )
    combined_details['dat_quangduong'] = {
        'name': 'Số Km thực hành DAT',
        'group': 'DAT',
        'status': 'PASS' if km_ok else 'FAIL',
        'value': f"{curr_km:.1f} / {target_km:.0f} km",
        'detail': km_info,
        'rule': f"Định mức tối thiểu {target_km:.0f} km",
        'exempt': False
    }
    if not km_ok:
        combined_failing.append(f"Số Km DAT ({curr_km:.1f}/{target_km:.0f} km)")

    # Mục DAT 2: Số Giờ thực hành
    missing_time_parts = []
    if not gio_ok:
        missing_time_parts.append(f"thiếu {student_record.get('thoigian_thieu', 0.0):.1f}h")
    if not dem_ok:
        missing_time_parts.append(f"thiếu đêm ({curr_dem:.1f}/{target_dem:.1f}h)")
    if target_td > 0.0 and not tudong_ok:
        missing_time_parts.append(f"thiếu TĐ ({curr_td:.1f}/{target_td:.1f}h)")

    time_info = f"Đã hoàn thành {curr_gio:.1f} / {target_gio:.0f}h" if all_time_ok else (
        f"Chưa đạt thời gian ({', '.join(missing_time_parts)})"
    )
    td_val_str = "Bỏ qua" if target_td == 0.0 else f"{curr_td:.1f}h"
    rule_td_str = "miễn trừ xe tự động" if target_td == 0.0 else f"tự động {target_td:.1f}h"
    combined_details['dat_thoigian'] = {
        'name': 'Thời gian thực hành DAT',
        'group': 'DAT',
        'status': 'PASS' if all_time_ok else 'FAIL',
        'value': f"{curr_gio:.1f} / {target_gio:.0f}h (Đêm: {curr_dem:.1f}h, TĐ: {td_val_str})",
        'detail': time_info,
        'rule': f"Tối thiểu {target_gio:.0f}h (gồm đêm; {rule_td_str})",
        'exempt': False
    }
    if not all_time_ok:
        combined_failing.append(f"Thời gian DAT ({', '.join(missing_time_parts) if missing_time_parts else 'chưa đủ giờ'})")

    # Mục DAT 3: Số Ngày chạy đường
    day_info = f"Đã hoàn thành {songay_str}" if songay_ok else f"Chưa đủ số ngày đường ({songay_str})"
    combined_details['dat_songay'] = {
        'name': 'Số ngày chạy đường DAT',
        'group': 'DAT',
        'status': 'PASS' if songay_ok else 'FAIL',
        'value': songay_str,
        'detail': day_info,
        'rule': "Tối thiểu 4 hoặc 5 ngày đường",
        'exempt': False
    }
    if not songay_ok:
        combined_failing.append(f"Số ngày đường ({songay_str})")

    # 2. Đánh giá nhóm Lý Thuyết & Hồ Sơ từ file PDF
    fname = student_record.get('lythuyet_pdf_filename')
    mahv = student_record.get('mahv', '')
    cccd = student_record.get('cccd', '')

    pdf_path = None
    if fname:
        candidate = os.path.join(downloads_dir, fname)
        if os.path.exists(candidate):
            pdf_path = candidate

    if not pdf_path and mahv:
        candidate = os.path.join(downloads_dir, f"{mahv}_lythuyet.pdf")
        if os.path.exists(candidate):
            pdf_path = candidate

    if not pdf_path and cccd:
        candidate = os.path.join(downloads_dir, f"{cccd}_lythuyet.pdf")
        if os.path.exists(candidate):
            pdf_path = candidate

    has_pdf = False
    lt_fails = []
    if pdf_path and os.path.exists(pdf_path):
        has_pdf = True
        car = extract_car_from_lythuyet_pdf(pdf_path)
        if car:
            student_record['xe_phan'] = car
        res = evaluate_pdf_file(pdf_path, exempt_all_cabin=exempt_all_cabin)
        pdf_details = res.get('details', {})
        for k, v in pdf_details.items():
            v['group'] = 'LY_THUYET'
            v['rule'] = 'Miễn trừ (Bỏ qua)' if v.get('exempt') else 'Bắt buộc đạt'
            combined_details[k] = v
        lt_fails = res.get('failing_items', [])
        for f_item in lt_fails:
            combined_failing.append(f_item)
    else:
        # Nếu chưa có PDF lý thuyết
        for key, name, y_pos, is_exempt_default in ROW_DEFINITIONS:
            is_exempt = is_exempt_default or (exempt_all_cabin and 'cabin' in key)
            combined_details[key] = {
                'name': name,
                'group': 'LY_THUYET',
                'status': 'NONE',
                'exempt': is_exempt,
                'rule': 'Miễn trừ (Bỏ qua)' if is_exempt else 'Bắt buộc đạt',
                'detail': 'Chưa tải báo cáo lý thuyết'
            }
        if student_record.get('lythuyet_pdf_url'):
            combined_failing.append("Đang chờ tải báo cáo lý thuyết")
        else:
            combined_failing.append("Chưa tra cứu lý thuyết (/lythuyet)")

    # Đánh giá riêng phần Lý thuyết
    if has_pdf:
        if len(lt_fails) == 0:
            student_record['lythuyet_dat'] = True
            student_record['lythuyet_status'] = 'Đạt'
            student_record['lythuyet_reason'] = 'Tất cả các môn lý thuyết đều tích xanh (đã miễn trừ cabin)'
        else:
            student_record['lythuyet_dat'] = False
            student_record['lythuyet_status'] = 'Không'
            student_record['lythuyet_reason'] = f"Chưa đạt các môn: {', '.join(lt_fails)}"
    else:
        student_record['lythuyet_dat'] = False
        if student_record.get('lythuyet_pdf_url'):
            student_record['lythuyet_status'] = 'Đang tải'
            student_record['lythuyet_reason'] = 'Đang chờ tải báo cáo lý thuyết'
        else:
            student_record['lythuyet_status'] = 'Chưa tra'
            student_record['lythuyet_reason'] = 'Chưa tra cứu lý thuyết (/lythuyet)'

    # 3. Kết luận tổng hợp điều kiện thi tốt nghiệp
    # ĐẠT = Hoàn thành đủ số km, đủ giờ, đủ ngày đường VÀ tất cả môn lý thuyết đều tích xanh (trừ hình cabin)
    is_totnghiep_dat = (len(combined_failing) == 0)

    student_record['totnghiep_details'] = combined_details
    student_record['totnghiep_failing'] = combined_failing
    student_record['totnghiep_has_pdf'] = has_pdf

    if is_totnghiep_dat:
        student_record['dieukien_totnghiep'] = 'Đạt'
        student_record['totnghiep_reason'] = 'Đủ điều kiện thi tốt nghiệp: Đủ Km, Giờ, Ngày đường thực hành DAT và các môn Lý thuyết (Miễn trừ hình cabin)'
    else:
        is_brand_new = (student_record.get('ketqua') == 'Chưa tra DAT') and (not has_pdf) and (not student_record.get('lythuyet_pdf_url'))
        if is_brand_new:
            student_record['dieukien_totnghiep'] = 'Chưa tra'
            student_record['totnghiep_reason'] = 'Chưa tra cứu DAT và Lý thuyết'
        else:
            student_record['dieukien_totnghiep'] = 'Không'
            student_record['totnghiep_reason'] = f"Chưa đạt: {', '.join(combined_failing)}"

    return student_record
