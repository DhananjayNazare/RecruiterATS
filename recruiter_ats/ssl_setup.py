"""HTTPS support: use your own SSL certificate, or auto-create a self-signed one.

Settings (.env):
  ARS_SSL=1                 serve over HTTPS (default for app.py / serve_https.py)
  ARS_SSL_CERT, ARS_SSL_KEY paths to your own certificate chain + private key (PEM), e.g. from
                            Let's Encrypt, your CA, or mkcert. When both are set they are used as-is.
  ARS_SSL_HOSTS             extra comma-separated names / IPs to put in the self-signed certificate
                            (e.g. ats.mycompany.local,192.168.1.20). The machine name, localhost,
                            127.0.0.1 and this machine's LAN IPs are always included.
The self-signed certificate is stored in certs/ and renewed automatically 30 days before it expires
or when the host names change.
"""
import datetime as dt
import ipaddress
import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CERT_DIR = BASE_DIR / "certs"
CERT_FILE = CERT_DIR / "ats-cert.pem"
KEY_FILE = CERT_DIR / "ats-key.pem"
HOSTS_FILE = CERT_DIR / "ats-hosts.txt"
TRUTHY = {"1", "true", "yes", "on"}


def ssl_enabled():
    return os.getenv("ARS_SSL", "0").strip().lower() in TRUTHY


def _local_ips():
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:   # the address used for outbound traffic (no packet is sent for a UDP connect)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return ips


def wanted_hosts():
    names = {"localhost", socket.gethostname()}
    ips = _local_ips()
    for extra in os.getenv("ARS_SSL_HOSTS", "").split(","):
        extra = extra.strip()
        if not extra:
            continue
        try:
            ips.add(str(ipaddress.ip_address(extra)))
        except ValueError:
            names.add(extra)
    return sorted(n for n in names if n), sorted(ips)


def _cert_is_current(names, ips):
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(CERT_FILE.read_bytes())
        left = cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)
        return left > dt.timedelta(days=30) and HOSTS_FILE.read_text().strip() == ",".join(names + ips)
    except Exception:
        return False


def _generate(names, ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RecruiterATS (self-signed)"),
                         x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "localhost")])
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    CERT_DIR.mkdir(exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    HOSTS_FILE.write_text(",".join(names + ips))


def get_ssl_files():
    """Return (cert_path, key_path) as strings, or None when HTTPS is switched off."""
    if not ssl_enabled():
        return None
    cert, key = os.getenv("ARS_SSL_CERT", "").strip(), os.getenv("ARS_SSL_KEY", "").strip()
    if cert or key:
        if not (cert and key):
            raise RuntimeError("Set both ARS_SSL_CERT and ARS_SSL_KEY (or neither to use a self-signed certificate).")
        for p in (cert, key):
            if not Path(p).is_file():
                raise RuntimeError(f"SSL file not found: {p}")
        return cert, key
    names, ips = wanted_hosts()
    if not (CERT_FILE.is_file() and KEY_FILE.is_file() and _cert_is_current(names, ips)):
        _generate(names, ips)
        print(f"[SSL] Created self-signed certificate: {CERT_FILE}")
    return str(CERT_FILE), str(KEY_FILE)


if __name__ == "__main__":     # used by shell scripts:  python -m recruiter_ats.ssl_setup
    files = get_ssl_files()
    print(f"{files[0]}\n{files[1]}" if files else "")
