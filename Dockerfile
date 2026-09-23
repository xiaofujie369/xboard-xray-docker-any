FROM golang:1.25-alpine AS builder
ARG SING_BOX_VERSION=1.12.25
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch v${SING_BOX_VERSION} https://github.com/SagerNet/sing-box.git .
RUN CGO_ENABLED=0 go build -trimpath -tags with_quic,with_utls,with_v2ray_api \
    -ldflags "-s -w -X github.com/sagernet/sing-box/constant.Version=${SING_BOX_VERSION}" \
    -o /sing-box ./cmd/sing-box
FROM alpine:3.22
RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /sing-box /usr/local/bin/sing-box
ENTRYPOINT ["sing-box"]
