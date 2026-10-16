"""HTTPS support: self-signed certificate creation, renewal rules and secure cookies."""
import datetime as dt
import ssl
import threading
import urllib.request

import pytest
from cryptography import x509

from recruiter_ats import ssl_setup


@pytest.fixture()
def certdir(tmp_path, monkeypatch):
    d = tmp_path / "certs"
    monkeypatch.setattr(ssl_setup, "CERT_DIR", d)
    monkeypatch.setattr(ssl_setup, "CERT_FILE", d / "c.pem")
    monkeypatch.setattr(ssl_setup, "KEY_FILE", d / "k.pem")
    monkeypatch.setattr(ssl_setup, "HOSTS_FILE", d / "h.txt")
    monkeypatch.setenv("ARS_SSL", "1")
    monkeypatch.delenv("ARS_SSL_CERT", raising=False)
    monkeypatch.delenv("ARS_SSL_KEY", raising=False)
    return d


def test_off_returns_none(monkeypatch):
    monkeypatch.setenv("ARS_SSL", "0")
    assert ssl_setup.get_ssl_files() is None


def test_self_signed_created_reused_and_renewed_on_host_change(certdir, monkeypatch):
    monkeypatch.setenv("ARS_SSL_HOSTS", "ats.example.test,10.1.2.3")
    cert, key = ssl_setup.get_ssl_files()
    c = x509.load_pem_x509_certificate(open(cert, "rb").read())
    san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName) and "ats.example.test" in san.get_values_for_type(x509.DNSName)
    assert any(str(i) == "10.1.2.3" for i in san.get_values_for_type(x509.IPAddress))
    assert c.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) > dt.timedelta(days=300)
    ssl.create_default_context().load_cert_chain(cert, key)            # key matches cert
    first = open(cert, "rb").read()
    ssl_setup.get_ssl_files(); assert open(cert, "rb").read() == first   # reused
    monkeypatch.setenv("ARS_SSL_HOSTS", "other.test")
    ssl_setup.get_ssl_files(); assert open(cert, "rb").read() != first   # re-issued


def test_own_certificate_paths_validated(certdir, monkeypatch, tmp_path):
    monkeypatch.setenv("ARS_SSL_CERT", str(tmp_path / "missing.pem"))
    with pytest.raises(RuntimeError):
        ssl_setup.get_ssl_files()
    monkeypatch.setenv("ARS_SSL_KEY", "")
    monkeypatch.setenv("ARS_SSL_CERT", "x")
    with pytest.raises(RuntimeError):
        ssl_setup.get_ssl_files()


def test_https_server_serves_health(certdir):
    from cheroot.wsgi import Server
    from cheroot.ssl.builtin import BuiltinSSLAdapter
    from recruiter_ats import app
    cert, key = ssl_setup.get_ssl_files()
    srv = Server(("127.0.0.1", 0), app); srv.ssl_adapter = BuiltinSSLAdapter(certificate=cert, private_key=key)
    srv.prepare(); port = srv.bind_addr[1]
    t = threading.Thread(target=srv.serve, daemon=True); t.start()
    try:
        ctx = ssl.create_default_context(cafile=cert)       # trusted only because we pin our own cert
        body = urllib.request.urlopen(f"https://localhost:{port}/health", context=ctx, timeout=10).read()
        assert body
    finally:
        srv.stop()
