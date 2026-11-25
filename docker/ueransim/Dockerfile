FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        make gcc g++ cmake git libsctp-dev lksctp-tools iproute2 iputils-ping ca-certificates \
        python3 python3-pip procps \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v3.2.6 https://github.com/aligungr/UERANSIM /opt/UERANSIM \
    && cd /opt/UERANSIM && make -j"$(nproc)"
COPY requirements-ue.txt /tmp/requirements-ue.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements-ue.txt
# ITU-T P.1203 reference implementation (optional; without it a documented fallback is used)
RUN pip3 install --no-cache-dir "itu-p1203 @ git+https://github.com/itu-p1203/itu-p1203.git" || echo "P.1203 not installed"
WORKDIR /lab
