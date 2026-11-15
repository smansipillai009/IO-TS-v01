FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY products ./products
ENV DATA_DIR=/data PORT=8000 DEMO_PRODUCTS_DIR=/srv/products
VOLUME /data
EXPOSE 8000
# one process: job state lives in SQLite and a thread pool; scale vertically for the hackathon
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
