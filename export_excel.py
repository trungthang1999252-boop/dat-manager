import os
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def export_students_to_excel(students_list, output_path=None):
    if not output_path:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        downloads_dir = os.path.join(base_dir, 'downloads')
        os.makedirs(downloads_dir, exist_ok=True)
        output_path = os.path.join(downloads_dir, 'Danh_Sach_Kiem_Tra_DAT.xlsx')

    wb = openpyxl.Workbook()

    # Styling elements
    font_header_title = Font(name="Times New Roman", size=14, bold=True, color="1E3A8A")
    font_sub_title = Font(name="Times New Roman", size=11, italic=True)
    font_col_header = Font(name="Times New Roman", size=10, bold=True, color="FFFFFF")
    font_cell = Font(name="Times New Roman", size=10)
    font_cell_bold = Font(name="Times New Roman", size=10, bold=True)
    font_summary_bold = Font(name="Times New Roman", size=11, bold=True)

    fill_primary = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_pass = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid") # Xanh lá
    fill_fail = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid") # Đỏ
    fill_warn = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid") # Vàng cam

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    headers = [
        "STT", "Mã học viên", "Họ và tên", "Ngày sinh", "Số CCCD",
        "Khóa học", "Hạng", "Tổng Km", "Đủ Km?", "Tổng Giờ",
        "Giờ đêm", "Giờ tự động", "Số ngày đường", "Tiến độ %", "Kết quả DAT",
        "ĐK Thi Tốt Nghiệp", "Chi Tiết Thiếu (Tốt Nghiệp)",
        "Báo cáo Phiên", "Báo cáo Lý Thuyết", "Ghi chú giáo viên"
    ]

    def render_student_sheet(ws, title_text, s_list):
        # 1. School Title
        ws.merge_cells('A1:T1')
        ws['A1'] = "TRƯỜNG CAO ĐẲNG CƠ ĐIỆN - XÂY DỰNG VÀ NÔNG LÂM TRUNG BỘ"
        ws['A1'].font = font_header_title
        ws['A1'].alignment = align_center

        ws.merge_cells('A2:T2')
        ws['A2'] = title_text
        ws['A2'].font = font_cell_bold
        ws['A2'].alignment = align_center

        ws.merge_cells('A3:T3')
        ws['A3'] = f"Thời điểm xuất báo cáo: {datetime.now().strftime('%d/%m/%Y %H:%M')} — Tổng số: {len(s_list)} học viên"
        ws['A3'].font = font_sub_title
        ws['A3'].alignment = align_center

        # 2. Table Headers
        row_idx = 5
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=h)
            cell.font = font_col_header
            cell.fill = fill_primary
            cell.alignment = align_center
            cell.border = thin_border
        ws.row_dimensions[row_idx].height = 28

        # 3. Data Rows
        for i, s in enumerate(s_list, 1):
            row_idx += 1
            ws.row_dimensions[row_idx].height = 22

            km_val = float(s.get('quangduong', 0))
            gio_val = float(s.get('thoigian', 0))
            dem_val = float(s.get('thoigian_dem', 0))
            td_val = float(s.get('thoigian_tudong', 0))
            songay = s.get('songay_str', '') or ('Đủ (5/5)' if s.get('songay_dat') else 'Chưa đủ')
            ketqua = s.get('ketqua', 'Chưa Đạt')
            pct = f"{s.get('overall_pct', 0.0):.1f}%"
            note = s.get('note', '')

            has_phien = bool(s.get('pdf_url'))
            has_lt = bool(s.get('lythuyet_pdf_url'))

            is_passed = ketqua == 'Đạt'
            is_km_ok = s.get('quangduong_dat', False)

            dk_totnghiep = s.get('dieukien_totnghiep', 'Chưa tra LT')
            is_tn_pass = (dk_totnghiep == 'Đạt')
            is_tn_fail = (dk_totnghiep == 'Không')
            tn_detail = ", ".join(s.get('totnghiep_failing', [])) if is_tn_fail else ("Đạt chuẩn thi tốt nghiệp" if is_tn_pass else "")

            vals = [
                (i, align_center, None),
                (s.get('mahv', ''), align_center, None),
                (s.get('hoten', ''), align_left, font_cell_bold),
                (s.get('ngaysinh', ''), align_center, None),
                (s.get('cccd', ''), align_center, None),
                (s.get('khoa', ''), align_center, None),
                (s.get('hang', ''), align_center, None),
                (km_val, align_right, None),
                ("ĐỦ" if is_km_ok else "THIẾU", align_center, fill_pass if is_km_ok else fill_fail),
                (gio_val, align_right, None),
                (dem_val, align_right, None),
                (td_val, align_right, None),
                (songay, align_center, fill_pass if s.get('songay_dat') else fill_warn),
                (pct, align_center, fill_pass if is_passed else None),
                (ketqua, align_center, fill_pass if is_passed else fill_fail),
                (dk_totnghiep, align_center, fill_pass if is_tn_pass else (fill_fail if is_tn_fail else fill_warn)),
                (tn_detail, align_left, None),
                ("ĐÃ CÓ PDF" if has_phien else "—", align_center, fill_pass if has_phien else None),
                ("ĐÃ CÓ PDF" if has_lt else "—", align_center, fill_pass if has_lt else None),
                (note, align_left, None)
            ]

            for col_idx, (v, aln, custom_style) in enumerate(vals, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=v)
                cell.font = font_cell if not isinstance(custom_style, Font) else custom_style
                cell.alignment = aln
                cell.border = thin_border
                if isinstance(custom_style, PatternFill):
                    cell.fill = custom_style

        # Auto Column Widths
        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = max(len(str(cell.value or '')) for cell in col)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 10)
        ws.column_dimensions['A'].width = 5
        ws.column_dimensions['B'].width = 24
        ws.column_dimensions['C'].width = 24
        ws.column_dimensions['P'].width = 25

    # 1. Sheet Tổng hợp
    ws_all = wb.active
    ws_all.title = "Tất_Cả_Học_Viên"
    render_student_sheet(ws_all, "BẢNG TỔNG HỢP THEO DÕI & KIỂM TRA ĐIỀU KIỆN CHẠY DAT TOÀN KHÓA", students_list)

    # 2. Tự động tạo Sheet riêng theo từng Khóa học
    courses = sorted(list(set(s.get('khoa') for s in students_list if s.get('khoa'))))
    for c in courses:
        # Title giới hạn 31 ký tự trong Excel
        safe_title = "".join(ch if ch.isalnum() or ch in '_-' else '_' for ch in c)[:30]
        c_students = [s for s in students_list if s.get('khoa') == c]
        if c_students:
            ws_c = wb.create_sheet(title=safe_title)
            render_student_sheet(ws_c, f"BẢNG KIỂM TRA ĐIỀU KIỆN CHẠY DAT — KHÓA HỌC: {c}", c_students)

    # 3. Sheet Thống kê Tóm tắt
    ws_sum = wb.create_sheet(title="Thống_Kê_Tóm_Tắt", index=0)
    ws_sum.merge_cells('A1:G1')
    ws_sum['A1'] = "BÁO CÁO THỐNG KÊ TỶ LỆ ĐẠT CHỈ TIÊU DAT THEO TỪNG KHÓA HỌC"
    ws_sum['A1'].font = font_header_title
    ws_sum['A1'].alignment = align_center

    sum_headers = ["STT", "Mã Khóa Học", "Tổng Số Học Viên", "Đủ Điều Kiện (Đạt)", "Thiếu Km/Giờ", "Thiếu Ngày Đường", "Tỷ Lệ Đạt %"]
    row_sum = 3
    for col_idx, h in enumerate(sum_headers, 1):
        cell = ws_sum.cell(row=row_sum, column=col_idx, value=h)
        cell.font = font_col_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = thin_border

    for i, c in enumerate(courses, 1):
        row_sum += 1
        c_list = [s for s in students_list if s.get('khoa') == c]
        tot = len(c_list)
        p = sum(1 for s in c_list if s.get('ketqua') == 'Đạt')
        m_km = sum(1 for s in c_list if s.get('quangduong_dat') is False or s.get('thoigian_dat') is False)
        m_days = sum(1 for s in c_list if s.get('songay_dat') is False and s.get('quangduong_dat') is True)
        rate = f"{(p/tot)*100:.1f}%" if tot > 0 else "0.0%"

        ws_sum.cell(row=row_sum, column=1, value=i).alignment = align_center
        ws_sum.cell(row=row_sum, column=2, value=c).font = font_cell_bold
        ws_sum.cell(row=row_sum, column=3, value=tot).alignment = align_center
        ws_sum.cell(row=row_sum, column=4, value=p).alignment = align_center
        ws_sum.cell(row=row_sum, column=5, value=m_km).alignment = align_center
        ws_sum.cell(row=row_sum, column=6, value=m_days).alignment = align_center
        ws_sum.cell(row=row_sum, column=7, value=rate).alignment = align_center

        for col_idx in range(1, 8):
            ws_sum.cell(row=row_sum, column=col_idx).border = thin_border

    for col in ws_sum.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws_sum.column_dimensions[col_letter].width = max(max_len + 4, 14)

    wb.save(output_path)
    return output_path

def export_roster_to_excel(roster_list, output_path=None):
    """Xuất danh sách học viên gốc ra file Excel chuẩn."""
    if not output_path:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        downloads_dir = os.path.join(base_dir, 'downloads')
        os.makedirs(downloads_dir, exist_ok=True)
        output_path = os.path.join(downloads_dir, 'Danh_Sach_Hoc_Vien_Goc.xlsx')

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Danh Sách Học Viên Gốc"

    font_header_title = Font(name="Times New Roman", size=14, bold=True, color="1E3A8A")
    font_col_header = Font(name="Times New Roman", size=10, bold=True, color="FFFFFF")
    font_cell = Font(name="Times New Roman", size=10)
    font_cell_bold = Font(name="Times New Roman", size=10, bold=True)
    fill_primary = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    ws.merge_cells('A1:G1')
    ws['A1'] = "DANH SÁCH HỌC VIÊN ĐÀO TẠO LÁI XE (HỒ SƠ GỐC BAN ĐẦU)"
    ws['A1'].font = font_header_title
    ws['A1'].alignment = align_center

    headers = ["STT", "Họ và Tên", "Số CCCD / CMND", "Ngày sinh", "Hạng Đào Tạo", "Khóa Học", "Mã Học Viên", "Số Điện Thoại"]
    ws.row_dimensions[3].height = 24
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_idx, value=h)
        cell.font = font_col_header
        cell.fill = fill_primary
        cell.alignment = align_center
        cell.border = thin_border

    for i, s in enumerate(roster_list, 1):
        r = 3 + i
        ws.row_dimensions[r].height = 20
        ws.cell(row=r, column=1, value=i).alignment = align_center
        ws.cell(row=r, column=2, value=s.get('hoten', '')).font = font_cell_bold
        ws.cell(row=r, column=2).alignment = align_left
        ws.cell(row=r, column=3, value=s.get('cccd', '')).alignment = align_center
        ws.cell(row=r, column=4, value=s.get('ngaysinh', '')).alignment = align_center
        ws.cell(row=r, column=5, value=s.get('hang', '')).alignment = align_center
        ws.cell(row=r, column=6, value=s.get('khoa', '')).alignment = align_left
        ws.cell(row=r, column=7, value=s.get('mahv', '')).alignment = align_center
        ws.cell(row=r, column=8, value=s.get('sdt', '')).alignment = align_center

        for col_idx in range(1, 9):
            ws.cell(row=r, column=col_idx).border = thin_border

    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    wb.save(output_path)
    return output_path
