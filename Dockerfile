FROM python:3.11-slim

WORKDIR /app

# Cài đặt các gói hệ thống cần thiết cho OpenCV và hình ảnh
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Cài đặt Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Sao chép toàn bộ mã nguồn
COPY . .

# Tạo thư mục dữ liệu
RUN mkdir -p data downloads uploads data/backups

ENV PORT=5050
EXPOSE 5050

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5050", "app:app", "--timeout", "120"]
