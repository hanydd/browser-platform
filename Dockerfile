FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV RESOLUTION=1280x800x24
ENV CHROME_PROFILE_DIR=/tmp/chrome-data

RUN sed -i 's@deb.debian.org@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    socat \
    xvfb \
    x11vnc \
    novnc \
    python3-websockify \
    supervisor \
    procps \
    curl \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-freefont-ttf \
    dbus-x11 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/chrome-data

COPY browserctl.sh /usr/local/bin/browserctl
COPY healthcheck.sh /usr/local/bin/browser-healthcheck
RUN chmod +x /usr/local/bin/browserctl /usr/local/bin/browser-healthcheck

COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 9222 6080

HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD ["/usr/local/bin/browser-healthcheck"]

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
