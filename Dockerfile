# VSD Pad server: the settings page and your layout on a server (Unraid, any Docker host).
# The pads stay plugged into your PC, which follows this server (see docker/README.md).
ARG BASE=python:3.12-slim
FROM ${BASE}

# DejaVu: key labels and symbols in the previews (the PC uses its Windows fonts for the pad itself)
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pillow==11.3.0

WORKDIR /app
COPY next/run.py ./run.py
COPY next/vsdpad ./vsdpad
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && find /app -name "__pycache__" -prune -exec rm -rf {} + \
 && mv /app/vsdpad/themes /app/themes-builtin

ENV PYTHONUNBUFFERED=1 \
    VSDPAD_PORT=8765
EXPOSE 8765
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('VSDPAD_PORT', '8765'), timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
