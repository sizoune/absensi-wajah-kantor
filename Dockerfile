FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --user-group --no-create-home camera

WORKDIR /app
COPY requirements.txt requirements-eoffice.txt ./
RUN python -m pip install -r requirements-eoffice.txt && python -m pip check
COPY . .
RUN mkdir -p /app/instance \
    && chown camera:camera /app/instance \
    && chmod 700 /app/instance \
    && chmod 755 /app/docker/entrypoint.sh

USER camera:camera
EXPOSE 5000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
