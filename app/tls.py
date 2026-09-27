"""HTTPS for the shop network, with the shop's own small certificate authority (CA).

Browsers only allow the microphone, home-screen installation and offline support on
trusted https pages. Public certificates can't be issued for a PC on a private network,
so the app creates:

* data/tls/ca.crt, ca-key.pem – a local CA, created once. Each phone installs ca.crt once
  (Settings → Phones & tablets shows a QR code); afterwards the app is fully trusted.
  The CA is name-constrained to private IP ranges, localhost, .local and this PC's name,
  so it cannot vouch for any internet site even if its key leaked.
* data/tls/cert.pem, key.pem – the server certificate, signed by the CA and renewed
  automatically when the PC's addresses change (phones keep trusting it).

    python -m app.tls    # create/refresh the certificates and print their location
"""

import datetime
import ipaddress
import os
import re
import socket

PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10"]
CA_NAME = "Atölye ERP Yerel Sertifika Kurumu"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _host_label():
    """This PC's name if it is a valid DNS label (certificate names must be), else None."""
    host = socket.gethostname().split(".")[0].lower()
    return host if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host) else None


def _valid_ip(ip):
    try:
        return ipaddress.ip_address(ip).version == 4
    except ValueError:
        return False


def _is_private(ip):
    addr = ipaddress.ip_address(ip)
    return any(addr in ipaddress.ip_network(n) for n in PRIVATE_NETS)


def local_addresses():
    """(DNS names, IPv4 addresses) of this PC that certificates may name: only private addresses."""
    host = _host_label()
    names = {"localhost"} | ({host, host + ".local"} if host else set())
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for outgoing traffic = the LAN address
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    # ERP_LAN_IP: the host's address when the app runs in a container (it only sees its own)
    ips.update(ip.strip() for ip in os.environ.get("ERP_LAN_IP", "").split(",") if ip.strip())
    return sorted(names), sorted(ip for ip in ips if _valid_ip(ip) and _is_private(ip))


def lan_addresses():
    """IPs that phones on the same network can use, most likely first."""
    _names, ips = local_addresses()
    lan = [ip for ip in ips if not ip.startswith(("127.", "169.254."))]
    pinned = [ip.strip() for ip in os.environ.get("ERP_LAN_IP", "").split(",")]
    return sorted(lan, key=lambda ip: (ip not in pinned, not ip.startswith("192.168."), ip))


def tls_dir(data_dir):
    return os.path.join(data_dir, "tls")


def ca_cert_path(data_dir):
    return os.path.join(tls_dir(data_dir), "ca.crt")


def _write(path, data, private=False):
    with open(path, "wb") as fh:
        fh.write(data)
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def ensure_ca(data_dir):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    d = tls_dir(data_dir)
    cert_path, key_path = ca_cert_path(data_dir), os.path.join(d, "ca-key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, "rb") as fh:
            ca = x509.load_pem_x509_certificate(fh.read())
        with open(key_path, "rb") as fh:
            key = serialization.load_pem_private_key(fh.read(), password=None)
        if ca.not_valid_after_utc > _now() + datetime.timedelta(days=60):
            return ca, key
    os.makedirs(d, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = _host_label()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{CA_NAME} ({socket.gethostname()})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atölye ERP")])
    # every name a server certificate may carry; anything else (any internet site) is refused by clients
    permitted = [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
    permitted += [x509.DNSName("localhost"), x509.DNSName("local")] + ([x509.DNSName(host)] if host else [])
    ca = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1)).not_valid_after(_now() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                     key_encipherment=False, data_encipherment=False, key_agreement=False,
                                     encipher_only=False, decipher_only=False), critical=True)
        # non-critical so clients that don't understand it still accept the chain; those that do enforce it
        .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()), private=True)
    _write(cert_path, ca.public_bytes(serialization.Encoding.PEM))
    # the server certificate must be re-issued by the new CA
    for f in ("cert.pem", "key.pem"):
        try:
            os.remove(os.path.join(d, f))
        except FileNotFoundError:
            pass
    return ca, key


def _permitted(ca, dns_name):
    from cryptography import x509

    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return True
    allowed = [g.value.lower() for g in nc.permitted_subtrees or [] if isinstance(g, x509.DNSName)]
    return any(dns_name == a or dns_name.endswith("." + a) for a in allowed)


def ensure_cert(data_dir):
    """Returns (cert_path, key_path) of a server certificate covering this PC's current names and IPs."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca, ca_key = ensure_ca(data_dir)
    d = tls_dir(data_dir)
    cert_path, key_path = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    names, ips = local_addresses()
    # a name outside the CA's constraints would make clients reject the whole certificate
    # (e.g. the PC was renamed after the CA was made): leave such names out, IPs still work
    names = [n for n in names if _permitted(ca, n)]
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            covered = set()
        still_valid = cert.not_valid_after_utc > _now() + datetime.timedelta(days=30)
        if still_valid and set(ips) <= covered and cert.issuer == ca.subject:
            return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _host_label() or "localhost"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Atölye ERP")])
    dns = [x509.DNSName(n) for n in names]
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(ca.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=397))  # Apple/Chrome limit for server certificates
        .add_extension(x509.SubjectAlternativeName(dns + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False,
                                     crl_sign=False, content_commitment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()), private=True)
    # full chain: some clients need the CA next to the leaf
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def ca_display_name(data_dir):
    """The name phones show for the certificate (iOS: Certificate Trust Settings)."""
    from cryptography.x509.oid import NameOID

    ca, _key = ensure_ca(data_dir)
    return ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def ca_fingerprint(data_dir):
    """SHA-1 thumbprint of the CA (what Windows' certificate store uses to identify it)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    with open(ca_cert_path(data_dir), "rb") as fh:
        return x509.load_pem_x509_certificate(fh.read()).fingerprint(hashes.SHA1()).hex()


def ca_der(data_dir):
    """The CA certificate in DER form (what Android and iOS install most smoothly)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    ensure_ca(data_dir)
    with open(ca_cert_path(data_dir), "rb") as fh:
        return x509.load_pem_x509_certificate(fh.read()).public_bytes(serialization.Encoding.DER)


if __name__ == "__main__":
    import sys

    from . import BASE_DIR

    data = os.path.abspath(os.environ.get("ERP_DATA_DIR", os.path.join(BASE_DIR, "data")))
    cert, key = ensure_cert(data)
    print(ca_cert_path(data))
    print(cert)
    sys.exit(0)
