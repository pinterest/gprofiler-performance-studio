FROM golang:1.24.0 AS build

ENV GO111MODULE on

WORKDIR /go

COPY go.* src/
COPY *.go src/
COPY conf src/conf

WORKDIR /go/src

RUN go get -v ./...
RUN go install -v ./...
RUN go build -v

FROM debian:12.10-slim

# Package source. Builds that cannot reach deb.debian.org (e.g. CI behind a DNS
# allowlist) pass --build-arg DEBIAN_MIRROR=<proxy that mirrors deb.debian.org>;
# "<mirror>/debian" and "<mirror>/debian-security" must exist under it. The slim
# image ships no CA bundle, so one is borrowed from the Go stage in case the
# mirror is https (apt-get install ca-certificates replaces it right after).
ARG DEBIAN_MIRROR=http://deb.debian.org
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates

RUN mkdir -p /data

COPY --from=build /go/src/main /indexer
COPY conf /conf

RUN useradd -m -s /bin/bash -u 888 non_root && \
    chown -R non_root:non_root /data && \
    chown -R non_root:non_root /indexer && \
    chown -R non_root:non_root /conf

USER non_root


ENTRYPOINT ["/indexer"]
