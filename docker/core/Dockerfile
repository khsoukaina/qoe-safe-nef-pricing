FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common gnupg ca-certificates curl git iproute2 iptables iputils-ping \
        tcpdump tshark python3 python3-pip python3-venv procps \
    && add-apt-repository -y ppa:open5gs/latest \
    && apt-get update && apt-get install -y --no-install-recommends open5gs \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-core.txt /tmp/requirements-core.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements-core.txt
# version check (PCF Npcf_PolicyAuthorization over SBI needs Open5GS >= 2.7.2)
RUN open5gs-pcfd -v || true
WORKDIR /lab
