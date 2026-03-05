# Python 3.12 軽量版 (Playwright不要)
FROM python:3.12-slim-bookworm

ENV LANG=ja_JP.UTF-8 \
    TZ=Asia/Tokyo \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
