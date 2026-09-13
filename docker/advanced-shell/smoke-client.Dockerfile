FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        openssh-client \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /smoke

COPY docker/bootstrap-advanced-shell-client.sh /usr/local/bin/bootstrap-advanced-shell-client
RUN chmod 0755 /usr/local/bin/bootstrap-advanced-shell-client

ENTRYPOINT ["/usr/local/bin/bootstrap-advanced-shell-client"]
CMD ["/bin/sleep", "infinity"]
