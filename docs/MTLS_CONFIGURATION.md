# mTLS Configuration for gProfiler Backend

## Overview

The gProfiler backend now supports mutual TLS (mTLS) authentication, allowing secure communication between agents and the backend with client certificate verification. This feature enables:

- **End-to-end encryption** of agent-backend communication
- **Mutual authentication** - both client and server verify each other's identity
- **Client certificate validation** for access control and auditing
- **Flexible deployment** - works with any PKI/certificate infrastructure

## Architecture

The mTLS implementation uses NGINX as the TLS termination point:

- **HTTPS server (port 443 by default)**: Handles mTLS connections with client certificate verification
- **HTTP server (port 80 by default)**: Maintains backward compatibility for legacy/development deployments
- **Automatic HTTPS detection**: HTTPS is automatically enabled when valid certificate files are detected at configured paths
- **Certificate auto-reload**: Supports short-lived certificates with periodic NGINX reloading

NGINX forwards the following client certificate information to the FastAPI backend via HTTP headers:
- `X-Client-DN`: Client certificate subject distinguished name
- `X-Client-Verify`: Certificate verification status (SUCCESS/FAILED)
- `X-Client-Serial`: Client certificate serial number

## Configuration

### Automatic HTTPS Detection

The backend automatically enables HTTPS/mTLS when it detects valid certificate files:

- **Certificate files exist and are readable** → HTTPS enabled (both port 443 and 80 active)
- **Certificate files missing or unreadable** → HTTP-only mode (only port 80 active)

This automatic detection means you don't need an explicit "enable HTTPS" flag - just provide certificates and HTTPS "just works".

### Environment Variables

All TLS configuration is controlled via environment variables, making it easy to integrate with different certificate management systems without code changes.

#### Certificate Paths

| Variable | Description | Default |
|----------|-------------|---------|
| `GPROFILER_TLS_CERT_PATH` | Path to server certificate chain (PEM format) | `/usr/src/app/certs/server.crt` |
| `GPROFILER_TLS_KEY_PATH` | Path to server private key (PEM format) | `/usr/src/app/certs/server.key` |
| `GPROFILER_TLS_CA_PATH` | Path to CA certificate for client verification (PEM format) | `/usr/src/app/certs/ca.crt` |

#### TLS Behavior

| Variable | Description | Default | Options |
|----------|-------------|---------|---------|
| `GPROFILER_TLS_VERIFY_CLIENT` | Client certificate verification mode | `optional` | `on` (require), `off` (disable), `optional` (allow but not require) |
| `HTTPS_PORT` | Port for HTTPS/mTLS server | `443` | Any valid port |
| `LISTEN_PORT` | Port for HTTP server (backward compatibility) | `80` | Any valid port |

#### Certificate Rotation

| Variable | Description | Default |
|----------|-------------|---------|
| `GPROFILER_ENABLE_CERT_RELOAD` | Enable automatic certificate reloading | `false` |
| `GPROFILER_CERT_RELOAD_PERIOD` | Reload period in seconds | `21600` (6 hours) |

**Note on certificate rotation**: When using short-lived certificates (e.g., 12-hour expiry), enable auto-reload and set the period to be less than the certificate lifetime. For example, with 12-hour certificates, use a 10-hour (36000 seconds) reload period.

### Docker Compose Example

#### With HTTPS/mTLS (Certificates Provided)

```yaml
services:
  webapp:
    environment:
      # TLS Certificate Paths - HTTPS automatically enabled when these files exist
      - GPROFILER_TLS_CERT_PATH=/path/to/server-chain.pem
      - GPROFILER_TLS_KEY_PATH=/path/to/server-key.pem
      - GPROFILER_TLS_CA_PATH=/path/to/ca-root.pem
      
      # Client Verification
      - GPROFILER_TLS_VERIFY_CLIENT=on
      
      # Certificate Auto-Reload (for short-lived certs)
      - GPROFILER_ENABLE_CERT_RELOAD=true
      - GPROFILER_CERT_RELOAD_PERIOD=36000  # 10 hours
      
      # Port Configuration (optional)
      - HTTPS_PORT=443
      - LISTEN_PORT=80
    
    volumes:
      # Mount your certificate directory
      - /etc/ssl/certs:/etc/ssl/certs:ro
```

#### HTTP-Only Mode (No Certificates)

```yaml
services:
  webapp:
    environment:
      # No certificate paths needed - automatically uses HTTP-only mode
      - LISTEN_PORT=80  # Optional: customize HTTP port
    # No certificate volume mounts needed
```

## Deployment Modes

### Development/Testing (Self-Signed Certificates)

For local development, you can generate self-signed certificates:

```bash
# Generate CA
openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
  -keyout ca-key.pem -out ca-cert.pem \
  -subj "/CN=Local CA"

# Generate server certificate
openssl req -newkey rsa:4096 -nodes \
  -keyout server-key.pem -out server-req.pem \
  -subj "/CN=localhost"

openssl x509 -req -in server-req.pem -days 365 \
  -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial \
  -out server-cert.pem

# Generate client certificate
openssl req -newkey rsa:4096 -nodes \
  -keyout client-key.pem -out client-req.pem \
  -subj "/CN=gprofiler-agent"

openssl x509 -req -in client-req.pem -days 365 \
  -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial \
  -out client-cert.pem
```

Set environment variables:
```bash
GPROFILER_TLS_CERT_PATH=./server-cert.pem
GPROFILER_TLS_KEY_PATH=./server-key.pem
GPROFILER_TLS_CA_PATH=./ca-cert.pem
GPROFILER_TLS_VERIFY_CLIENT=optional  # Use 'on' to require client certs
```

### Production (Enterprise PKI)

In production environments with existing PKI infrastructure:

1. **Obtain certificates** from your organization's certificate authority
2. **Mount certificate paths** into the container via volumes
3. **Set certificate paths** via environment variables (HTTPS will auto-enable)
4. **Configure verification mode**: Use `GPROFILER_TLS_VERIFY_CLIENT=on` to enforce client authentication
5. **Enable auto-reload** if using short-lived certificates

**Note**: HTTPS is automatically enabled when the certificate and key files exist at the configured paths. No explicit "enable HTTPS" flag is needed.

### HTTP-Only Deployment

For deployments that don't need TLS/mTLS:

1. **Don't set certificate environment variables**, or set them to non-existent paths
2. The backend automatically detects missing certificates and runs in HTTP-only mode
3. Only port 80 will be active

```bash
# No certificate configuration needed
# Backend automatically runs in HTTP-only mode
LISTEN_PORT=80
```

### Optional Client Certificates

To support both authenticated and unauthenticated clients:

```bash
GPROFILER_TLS_VERIFY_CLIENT=optional
```

This allows:
- Clients with valid certificates to be authenticated (identity available in `X-Client-DN` header)
- Clients without certificates to connect anonymously
- Backend can implement custom authorization logic based on certificate presence

## Load Balancer Configuration

The included NGINX load balancer (`deploy/https_nginx.conf`) supports:

- **Port 8082**: HTTP proxy to webapp (no TLS)
- **Port 443**: HTTPS with TLS termination at load balancer
- **Port 8083**: TLS passthrough (encrypted traffic forwarded to webapp:443 without decryption)

For mTLS to work end-to-end, use **port 8083** which forwards the encrypted traffic directly to the webapp container where client certificate verification occurs.

## Security Considerations

### Certificate Chain Depth

The configuration supports certificate chains up to depth 10 (`ssl_verify_depth 10`), accommodating complex PKI hierarchies with multiple intermediate CAs.

### TLS Protocol Versions

Only modern TLS protocols are enabled:
- TLS 1.2
- TLS 1.3

Older protocols (SSLv3, TLS 1.0, TLS 1.1) are disabled for security.

### Cipher Suites

The configuration uses strong, forward-secret cipher suites:
- `EECDH+AESGCM` (Elliptic Curve Diffie-Hellman with AES-GCM)
- `EDH+AESGCM` (Ephemeral Diffie-Hellman with AES-GCM)

### Client Certificate Information

When client certificates are present, the backend receives:
- **Subject DN** (`X-Client-DN`): Use for identity/authorization
- **Verification status** (`X-Client-Verify`): Always check this is "SUCCESS"
- **Serial number** (`X-Client-Serial`): For audit logging

Example FastAPI middleware to extract client identity:

```python
from fastapi import Request

@app.middleware("http")
async def log_client_identity(request: Request, call_next):
    client_dn = request.headers.get("X-Client-DN")
    client_verify = request.headers.get("X-Client-Verify")
    
    if client_verify == "SUCCESS" and client_dn:
        # Client is authenticated
        logger.info(f"Authenticated request from: {client_dn}")
    
    response = await call_next(request)
    return response
```

## Troubleshooting

### "Certificate chain too long" error

If you see errors about certificate chain depth:
- Check your certificate chain length
- Increase `ssl_verify_depth` in `nginx.conf` if needed (current default: 10)

### "No required SSL certificate was sent"

This occurs when:
- `GPROFILER_TLS_VERIFY_CLIENT=on` but client doesn't send a certificate
- Solution: Set to `optional` or ensure clients are configured with certificates

### Certificates not reloading

If certificates aren't updating after rotation:
- Verify `GPROFILER_ENABLE_CERT_RELOAD=true`
- Check reload period is appropriate for certificate lifetime
- Review logs for reload errors: `docker logs <container> | grep -i reload`

### Connection refused on port 443

Check if NGINX started successfully:
```bash
docker logs <container> | grep -i nginx
```

Common causes:
- Missing certificate files at configured paths
- Invalid certificate format (must be PEM)
- Permission issues reading certificate files

## Migration from Non-TLS Deployment

To migrate an existing gProfiler deployment to mTLS:

1. **Deploy with optional client verification first**:
   ```bash
   GPROFILER_TLS_VERIFY_CLIENT=optional
   ```
   This allows both HTTP and HTTPS connections during migration.

2. **Update agents** to use HTTPS endpoint and provide client certificates

3. **Monitor** the `X-Client-Verify` header to confirm agents are authenticating

4. **Enforce mTLS** once all agents are upgraded:
   ```bash
   GPROFILER_TLS_VERIFY_CLIENT=on
   ```

5. **Optional**: Disable HTTP server by removing the HTTP server block from nginx.conf

## Benefits

- **Zero-trust security**: Mutual authentication ensures both parties are who they claim to be
- **Compliance**: Meet security requirements for encrypted agent-server communication
- **Auditability**: Track which clients (identified by certificate DN) are accessing the service
- **Flexibility**: Works with any PKI infrastructure (internal CA, Let's Encrypt, commercial CA, etc.)
- **Minimal overhead**: TLS termination at NGINX with efficient connection reuse
- **Automatic detection**: HTTPS automatically enables when certificates are present - no explicit flags needed
- **Backward compatible**: HTTP endpoint can remain active during migration
- **Simple configuration**: Just provide certificates and mTLS "just works"
