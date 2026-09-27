FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ERP_DATA_DIR=/data TZ=Europe/Istanbul
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py .
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8080"]
