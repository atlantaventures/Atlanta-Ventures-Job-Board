FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt && playwright install --with-deps chromium

COPY . .

CMD ["sh", "-c", "python3 setup_env.py && python3 sync/webhook.py"]
