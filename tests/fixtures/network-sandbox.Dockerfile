FROM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

RUN apk add --no-cache python3 bash curl iptables ip6tables iproute2
RUN mkdir -p /workspace
CMD ["tail", "-f", "/dev/null"]
