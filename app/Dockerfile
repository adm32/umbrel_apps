FROM python:3.12-slim-bookworm

# smartmontools: drive health · iproute2/iputils/traceroute/nmap/iw: network tools
# util-linux: nsenter (run tools in the host's network namespace) · procps/coreutils: du, ps
# ffmpeg: MP3/WAV/MP4 conversion for the downloader
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      smartmontools iproute2 iputils-ping traceroute nmap iw util-linux procps ca-certificates ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Deno: the JavaScript runtime yt-dlp needs to solve YouTube's challenges
ARG TARGETARCH
ARG DENO_VERSION=2.9.8
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in arm64) arch=aarch64 ;; *) arch=x86_64 ;; esac; \
    python -c "import urllib.request,zipfile,io; z=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen('https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${arch}-unknown-linux-gnu.zip').read())); z.extract('deno','/usr/local/bin')"; \
    chmod +x /usr/local/bin/deno; \
    deno --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vitals ./vitals
COPY web ./web

ENV PYTHONUNBUFFERED=1 PORT=3000 DENO_NO_UPDATE_CHECK=1
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3000/api/system',timeout=4)" || exit 1
CMD ["python", "-m", "vitals"]
