ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/tracker-venv PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    DISPLAY=:99 DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl supervisor xvfb fluxbox x11vnc novnc websockify x11-utils \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir uv==0.12.19
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && /opt/tracker-venv/bin/playwright install --with-deps chromium \
    && rm -rf /root/.cache /var/lib/apt/lists/*
COPY src ./src
RUN uv sync --frozen --no-dev
RUN apt-get update && apt-get install -y --no-install-recommends libnss3-tools \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 tracker \
    && mkdir -p /data /tmp/supervisor && chown tracker:tracker /data /tmp/supervisor \
    && chmod 700 /data
# Chromium's Linux trust store is separate from the system CA bundle.
RUN mkdir -p /home/tracker/.local/share/pki/nssdb \
    && certutil -N --empty-password -d sql:/home/tracker/.local/share/pki/nssdb \
    && for certificate in /usr/local/share/ca-certificates/*.crt; do \
         [ -f "$certificate" ] || continue; \
         certutil -A -d sql:/home/tracker/.local/share/pki/nssdb \
           -n "$(basename "$certificate")" -t 'C,,' -i "$certificate" || exit 1; \
       done \
    && chown -R tracker:tracker /home/tracker/.local
RUN dpkg-query -W -f='{"version":"${Version}"}' novnc > /usr/share/novnc/package.json
COPY docker/fluxbox-init /etc/fluxbox/tracker-init
COPY docker/novnc_proxy.py /app/docker/novnc_proxy.py
COPY docker/supervisord.conf /etc/supervisor/conf.d/tracker.conf
USER tracker
ENV PATH=/opt/tracker-venv/bin:$PATH
EXPOSE 8080 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/tracker.conf"]
