FROM golang:1.26-alpine AS build

WORKDIR /src/go
COPY go/go.mod go/go.sum ./
RUN go mod download
COPY go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/control-plane ./cmd/control-plane \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/outbox-relay ./cmd/outbox-relay \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/llm-gateway ./cmd/llm-gateway

FROM alpine:3.22
RUN addgroup -S finevision && adduser -S -G finevision finevision
COPY --from=build /out /usr/local/bin
USER finevision
ENTRYPOINT ["/usr/local/bin/control-plane"]
