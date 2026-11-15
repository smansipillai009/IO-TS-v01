FROM python:3.11-slim

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY products ./products
COPY run.py ./run.py

ENV DATA_DIR=/srv/data \
    DEMO_PRODUCTS_DIR=/srv/products \
    PYTHONUNBUFFERED=1

RUN mkdir -p /srv/data

EXPOSE 8000

CMD ["python", "run.py"]
