# Self-contained renderer: node + Debian's chromium + a local mermaid-cli.
# Deterministic chrome path (/usr/bin/chromium) — no fighting a base image's
# puppeteer cache. Renders "Mermaid source -> PNG (handDrawn/classic)".
FROM node:20-bookworm-slim

# chromium + the fonts mermaid needs for legible labels.
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium \
      fonts-liberation \
      fonts-dejavu-core \
      fonts-noto-color-emoji \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Don't let puppeteer download its own chrome — we use the apt one.
ENV PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_CONFIG=/srv/puppeteer-config.json \
    PORT=8080

COPY services/mermaid_renderer/package.json ./package.json
RUN npm install --omit=dev

COPY services/mermaid_renderer/server.mjs ./server.mjs
COPY services/mermaid_renderer/puppeteer-config.json ./puppeteer-config.json

# Fail the build early if chromium isn't where we expect.
RUN test -x /usr/bin/chromium

CMD ["node", "server.mjs"]
