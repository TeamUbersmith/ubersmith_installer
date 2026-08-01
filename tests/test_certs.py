"""Tests for ubersmith_installer.certs."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ubersmith_installer.certs import RSA_KEY_SIZE, generate_selfsigned_cert


def test_generates_key_csr_and_pem_files(tmp_path: Path) -> None:
    hostname = "billing.example.com"
    generate_selfsigned_cert(hostname, tmp_path)

    key_path = tmp_path / f"{hostname}.key"
    csr_path = tmp_path / f"{hostname}.csr"
    pem_path = tmp_path / f"{hostname}.pem"

    assert key_path.is_file()
    assert csr_path.is_file()
    assert pem_path.is_file()

    assert key_path.read_bytes().startswith(b"-----BEGIN")
    assert csr_path.read_bytes().startswith(b"-----BEGIN CERTIFICATE REQUEST")
    assert pem_path.read_bytes().startswith(b"-----BEGIN CERTIFICATE")


def test_pem_common_name_matches_hostname(tmp_path: Path) -> None:
    from cryptography import x509

    hostname = "portal.example.org"
    generate_selfsigned_cert(hostname, tmp_path)

    pem_bytes = (tmp_path / f"{hostname}.pem").read_bytes()
    cert = x509.load_pem_x509_certificate(pem_bytes)

    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert len(common_names) == 1
    assert common_names[0].value == hostname

    orgs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    assert orgs[0].value == "Ubersmith"

    units = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
    assert units[0].value == "Hosting"

    # Self-signed: issuer == subject.
    assert cert.issuer == cert.subject


def test_private_key_is_rsa_4096(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization

    hostname = "mail.example.net"
    generate_selfsigned_cert(hostname, tmp_path)

    key_bytes = (tmp_path / f"{hostname}.key").read_bytes()
    private_key = serialization.load_pem_private_key(key_bytes, password=None)

    assert isinstance(private_key, rsa.RSAPrivateKey)
    assert private_key.key_size == RSA_KEY_SIZE == 4096


def test_creates_ssl_dir_if_missing(tmp_path: Path) -> None:
    ssl_dir = tmp_path / "conf" / "ssl"
    assert not ssl_dir.exists()

    generate_selfsigned_cert("newhost.example.com", ssl_dir)

    assert ssl_dir.is_dir()
    assert (ssl_dir / "newhost.example.com.pem").is_file()


def test_multiple_hostnames_get_independent_certs(tmp_path: Path) -> None:
    generate_selfsigned_cert("a.example.com", tmp_path)
    generate_selfsigned_cert("b.example.com", tmp_path)

    for hostname in ("a.example.com", "b.example.com"):
        assert (tmp_path / f"{hostname}.key").is_file()
        assert (tmp_path / f"{hostname}.csr").is_file()
        assert (tmp_path / f"{hostname}.pem").is_file()
