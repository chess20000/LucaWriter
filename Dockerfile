FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/main.py /app/backend/main.py
COPY frontend/index.html /app/frontend/index.html
COPY frontend/login.html /app/frontend/login.html

RUN mkdir -p /app/usrdata

EXPOSE 20000

ENV PYTHONUNBUFFERED=1

CMD ["python", "backend/main.py"]
