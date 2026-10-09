# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/signal HOME=/tmp/profile PLAYWRIGHT_BROWSERS_PATH=/opt/chromium
COPY deploy/browser-worker/requirements.txt /opt/browser-requirements.txt
RUN pip install --no-cache-dir -r /opt/browser-requirements.txt \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache \
    && groupadd --gid 10001 signal \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/profile --shell /usr/sbin/nologin signal
WORKDIR /opt/signal
COPY services/control_plane/src/signal_core/__init__.py services/control_plane/src/signal_core/crawl_urls.py services/control_plane/src/signal_core/crawl_http.py services/control_plane/src/signal_core/browser_policy.py services/control_plane/src/signal_core/browser_worker_runtime.py services/control_plane/src/signal_core/browser_proxy_runtime.py /opt/signal/signal_core/
USER 10001:10001
ENTRYPOINT ["python", "-m"]
