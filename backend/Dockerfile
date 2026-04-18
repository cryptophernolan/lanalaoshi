FROM python:3.11-slim

WORKDIR /app

# Chromium system deps cho Playwright (Debian trixie)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libxshmfence1 \
    libpango-1.0-0 libcairo2 libgdk-pixbuf-xlib-2.0-0 \
    libasound2t64 libx11-xcb1 libxcb-dri3-0 \
    fonts-liberation fonts-noto-color-emoji \
    ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY . .

EXPOSE 8000

CMD ["python", "main.py"]
