#!/bin/sh
# Self-signed TLS cert so the clipboard API works: browsers only expose
# navigator.clipboard over https:// or localhost. The browser warns once;
# "Advanced -> Proceed" is expected and fine on a lab network.
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/tls
mkdir -p "$DIR"

# Build the SAN list: DNS:host plus every local IPv4 as IP:...
SAN="DNS:$(hostname),DNS:rack-manager"
for ip in $(hostname -I 2>/dev/null); do
  case "$ip" in
    169.254.*) continue ;;   # link-local (APIPA) is useless in a cert
    *:*) continue ;;         # skip IPv6 to keep the string simple
  esac
  SAN="$SAN,IP:$ip"
done

openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
  -subj "/CN=rack-manager" -addext "subjectAltName=$SAN" 2>/dev/null

chmod 600 "$DIR/key.pem"
echo "wrote $DIR/cert.pem and $DIR/key.pem"
openssl x509 -in "$DIR/cert.pem" -noout -subject -enddate
