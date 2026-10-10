# syntax=docker/dockerfile:1
#
# Relay Event Analyst as one locked-down application container.
#
#   docker build -f docker/Dockerfile -t relay-event-analyst:0.1.0 .        # on the Jetson: arm64
#   docker buildx build --platform linux/arm64,linux/amd64 -f docker/Dockerfile .
#
# The language model is not in this image. It runs in Ollama on the host, where it
# can use the Jetson GPU, and the app reaches it on 127.0.0.1 only (see compose.yaml).
# The image holds the C++ core, the Python analyst (standard library + pypdf) and
# nothing that listens on a port.

ARG PY_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------- build the physics core
FROM ${PY_IMAGE} AS core
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ cmake make \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY core/ core/
RUN cmake -S core -B build -DCMAKE_BUILD_TYPE=Release -DREA_BUILD_TESTS=OFF -DREA_WERROR=ON \
 && cmake --build build -j"$(nproc)" \
 && strip build/rea-core \
 && ./build/rea-core --version

# ---------------------------------------------------------------- runtime
FROM ${PY_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REA_CORE=/usr/local/bin/rea-core \
    REA_PAPERS=/papers \
    REA_INDEX_DIR=/tmp
COPY --from=core /src/build/rea-core /usr/local/bin/rea-core
COPY pyproject.toml README.md LICENSE /opt/rea/
COPY python/ /opt/rea/python/
RUN pip install "/opt/rea[pdf]" \
 && rm -rf /opt/rea/python /root/.cache \
 && useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin rea \
 && mkdir -p /out /events /papers \
 && chown rea:rea /out
USER 10001:10001
WORKDIR /out
ENTRYPOINT ["rea"]
CMD ["doctor"]
