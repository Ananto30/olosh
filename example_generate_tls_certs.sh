#!/bin/bash

set -e

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <certs_dir> <server_ip>"
    exit 1
fi

CERT_DIR="$1"
SERVER_IP="$2"
mkdir -p "$CERT_DIR"

echo "Generating CA key and certificate..."
openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" -sha256 -days 3650 -out "$CERT_DIR/ca.crt" -subj "/CN=olosh-ca"

echo "Generating server key and CSR..."
openssl genrsa -out "$CERT_DIR/server.key" 4096
openssl req -new -key "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" -subj "/CN=olosh-server"

echo "Creating server certificate extension config with SAN for IP $SERVER_IP..."
cat > "$CERT_DIR/server_ext.cnf" <<EOF
subjectAltName = IP:$SERVER_IP
EOF

echo "Signing server certificate with CA..."
openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial -out "$CERT_DIR/server.crt" -days 3650 -sha256 -extfile "$CERT_DIR/server_ext.cnf"

echo "Generating client key and CSR..."
openssl genrsa -out "$CERT_DIR/client.key" 4096
openssl req -new -key "$CERT_DIR/client.key" -out "$CERT_DIR/client.csr" -subj "/CN=olosh-client"

echo "Signing client certificate with CA..."
openssl x509 -req -in "$CERT_DIR/client.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial -out "$CERT_DIR/client.crt" -days 3650 -sha256

echo "Certificates generated in $CERT_DIR:"
ls -l "$CERT_DIR"