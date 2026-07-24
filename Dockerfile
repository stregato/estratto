FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ESTRATTO_CONFIG_PATH=/data/config.yaml

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY estratto /app/estratto
COPY web /app/web
COPY config.yaml /app/config.yaml
COPY docker/docker-entrypoint.sh /usr/local/bin/estratto-entrypoint

RUN chmod +x /usr/local/bin/estratto-entrypoint

WORKDIR /data

VOLUME ["/data"]
EXPOSE 8001

ENTRYPOINT ["estratto-entrypoint"]
CMD ["web"]
