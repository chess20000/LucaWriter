FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/main.py /app/backend/main.py
COPY backend/icon_generator.py /app/backend/icon_generator.py
COPY frontend/index.html /app/frontend/index.html
COPY frontend/login.html /app/frontend/login.html
COPY frontend/icon.png /app/frontend/icon.png
COPY frontend/icon.ico /app/frontend/icon.ico
COPY icon_template.png /app/icon_template.png
COPY icon_gold.png /app/icon_gold.png

RUN mkdir -p /app/usrdata

EXPOSE 20000

ENV PYTHONUNBUFFERED=1

CMD ["python", "backend/main.py"]
