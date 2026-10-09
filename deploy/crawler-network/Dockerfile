# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

RUN groupadd --gid 10001 signal \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin signal

ENV HOME=/nonexistent \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/signal

WORKDIR /opt/signal
COPY --chown=10001:10001 services/control_plane/src/signal_core/__init__.py /opt/signal/signal_core/__init__.py
COPY --chown=10001:10001 services/control_plane/src/signal_core/crawl_urls.py /opt/signal/signal_core/crawl_urls.py
COPY --chown=10001:10001 services/control_plane/src/signal_core/crawl_http.py /opt/signal/signal_core/crawl_http.py
COPY --chown=10001:10001 deploy/crawler-network/server.py /opt/signal/crawler-network-server.py

USER 10001:10001
ENTRYPOINT ["python"]
