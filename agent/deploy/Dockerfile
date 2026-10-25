FROM golang:1.26-alpine AS build
WORKDIR /src/agent
COPY agent/go.mod agent/go.sum ./
RUN go mod download
COPY agent ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/agent-service ./cmd/agent-service
FROM alpine:3.22
RUN apk add --no-cache ca-certificates && addgroup -S agent && adduser -S -G agent agent
COPY --from=build /out/agent-service /usr/local/bin/agent-service
USER agent
EXPOSE 8091
ENTRYPOINT ["/usr/local/bin/agent-service"]
