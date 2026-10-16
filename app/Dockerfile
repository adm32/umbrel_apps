FROM python:3.12-slim-bookworm

# smartmontools: drive health · iproute2/iputils/traceroute/nmap/iw: network tools
# util-linux: nsenter (run tools in the host's network namespace) · procps/coreutils: du, ps
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      smartmontools iproute2 iputils-ping traceroute nmap iw util-linux procps ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vitals ./vitals
COPY web ./web

ENV PYTHONUNBUFFERED=1 PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:3000/api/system',timeout=4)" || exit 1
CMD ["python", "-m", "vitals"]
