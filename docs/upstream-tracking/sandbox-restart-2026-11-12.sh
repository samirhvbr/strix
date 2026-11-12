#!/bin/sh
# Run after pulling this exact image. Only a disposable offline container is used.
set -eu
image='ghcr.io/samirhvbr/strix-sandbox:1.4.1-shvia-ac827485f0ca@sha256:c8d87cdefe834c9e7829fc571ceba84b57c0a583f4f0e61c3ef749f01c894f30'
name="strix-nss-restart-$$"
container=''
trap 'test -z "$container" || docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
wait_ready() {
  attempt=0
  while [ "$attempt" -lt 45 ]; do
    if docker exec "$container" sh -c 'test "$(cat /proc/1/comm)" = sleep' 2>/dev/null; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  docker logs --tail 40 "$container"
  return 1
}
check_trust() {
  trust=$(docker exec "$container" timeout 15 certutil -L -d sql:/home/pentester/.pki/nssdb)
  printf '%s\n' "$trust"
  printf '%s\n' "$trust" | grep -q 'Testing Root CA'
  printf '%s\n' "$trust" | grep -q 'Strix Offline Restart Fixture'
}
container=$(docker run -d --name "$name" --network none --log-opt max-size=1m --log-opt max-file=1 "$image" sleep infinity)
wait_ready
docker exec "$container" sh -ceu '
  mkdir -p /tmp/strix-nss-check
  timeout 15 openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /tmp/strix-nss-check/key.pem -out /tmp/strix-nss-check/cert.pem \
    -days 1 -subj "/CN=Strix Offline Restart Fixture" 2>/dev/null
  timeout 15 certutil -A -n "Strix Offline Restart Fixture" -t "C,," \
    -i /tmp/strix-nss-check/cert.pem -d sql:/home/pentester/.pki/nssdb
  rm /tmp/strix-nss-check/key.pem
'
check_trust
docker stop -t 2 "$container" >/dev/null
docker start "$container" >/dev/null
wait_ready
check_trust
printf 'PASS: same container %s reached sleep after both starts and retained both CAs\n' "$container"
