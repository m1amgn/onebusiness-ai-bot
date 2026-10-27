ARG TARGETARCH
FROM ghcr.io/ggml-org/whisper.cpp:main@sha256:cdfceef9caa5fed3efaf07bfc7d1ea0e43d28536e1521f448570b0ab02886abb AS whisper-amd64
FROM ghcr.io/ggml-org/whisper.cpp:main-arm64@sha256:9ddd3922278dc4cf7d9bb14b2c3de8c12c1fd825c8db915763b0c034475cf4ce AS whisper-arm64
FROM whisper-${TARGETARCH} AS runtime

RUN useradd --create-home --uid 10001 whisper \
    && mkdir -p /models \
    && chown 10001:10001 /models
COPY --chmod=755 docker/download-whisper-model.sh /usr/local/bin/download-whisper-model
USER 10001:10001
EXPOSE 8080
