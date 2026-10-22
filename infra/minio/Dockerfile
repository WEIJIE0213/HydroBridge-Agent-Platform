FROM golang:1.25-alpine AS builder
WORKDIR /src
ARG GOPROXY=https://proxy.golang.org,direct
ARG GODEBUG

# Official RELEASE.2025-10-15T17-29-55Z. The former Docker Hub image is
# unavailable; build the archived upstream release at an immutable commit.
ADD https://github.com/minio/minio/archive/9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a.tar.gz /tmp/minio-source.tar.gz
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    tar -xzf /tmp/minio-source.tar.gz --strip-components=1 -C /src \
    && (CGO_ENABLED=0 go build -p 2 -trimpath -o /out/minio . \
        || CGO_ENABLED=0 go build -p 2 -trimpath -o /out/minio . \
        || CGO_ENABLED=0 go build -p 2 -trimpath -o /out/minio .)

FROM alpine:3.22
RUN apk add --no-cache ca-certificates curl \
    && addgroup -S -g 10001 minio \
    && adduser -S -D -u 10001 -G minio minio \
    && mkdir /data \
    && chown minio:minio /data
COPY --from=builder /out/minio /usr/local/bin/minio
COPY --from=builder /src/LICENSE /licenses/minio-LICENSE
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
