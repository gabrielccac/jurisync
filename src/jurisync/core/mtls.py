"""Minimal HTTPS/1.1 over tlslite-ng with a client certificate whose key never leaves an A3 token.

Court login endpoints ask for the client certificate in two ways, and both are handled on one connection:

* in the first handshake (Keycloak X.509 hosts such as TJRS's), which tlslite does natively;
* through a server-initiated renegotiation after the request (the Certisign authenticator used by eproc
  portals: HelloRequest, then a second handshake with CertificateRequest). tlslite refuses renegotiation,
  so ``RenegotiatingTLSConnection`` answers one HelloRequest with a full handshake that carries the RFC 5746
  ``renegotiation_info`` (client_verify_data) instead of the SCSV.

TLS 1.2 only: the token signs with ``CKM_RSA_PKCS``, i.e. RSA PKCS#1 v1.5 over a DigestInfo.
"""

import socket
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlencode, urljoin, urlsplit

import certifi
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.verification import PolicyBuilder, Store
from tlslite import X509, HandshakeSettings, TLSConnection, X509CertChain
from tlslite.constants import CipherSuite, ContentType, ExtensionType, HandshakeType
from tlslite.errors import TLSAbruptCloseError
from tlslite.extensions import RenegotiationInfoExtension
from tlslite.messages import ClientHello, Finished
from tlslite.utils.rsakey import RSAKey

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
)
MAX_BODY = 20_000_000


@dataclass
class Response:
    url: str
    status: int
    headers: list = field(default_factory=list)  # [(name, value)]
    body: bytes = b""
    certificate_sent: bool = False

    def header(self, name):
        return next((value for key, value in self.headers if key.lower() == name.lower()), None)

    def cookies(self):
        result = {}
        for name, value in self.headers:
            if name.lower() == "set-cookie" and "=" in value:
                key, _, rest = value.partition("=")
                result[key.strip()] = rest.split(";", 1)[0]
        return result

    @property
    def location(self):
        value = self.header("Location")
        return urljoin(self.url, value) if value else None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


class TokenRSAKey(RSAKey):
    """tlslite RSA key whose private operation is a signature made inside the A3 token."""

    def __init__(self, signer):
        public = signer.chain[0].public_key().public_numbers()
        self.n, self.e = public.n, public.e
        self.key_type = "rsa"
        self._key_hash = None
        self.signer = signer
        self.signatures = 0

    def hasPrivateKey(self):
        return True

    def sign(self, bytes, padding="pkcs1", hashAlg=None, saltLen=None):
        # In TLS 1.2 tlslite hands over the DigestInfo already (hashAlg=None); the token applies the padding.
        if padding.lower() != "pkcs1":
            raise ValueError(f"RSA scheme not supported by the token key: {padding}")
        if hashAlg is not None:
            bytes = self.addPKCS1Prefix(bytes, hashAlg)
        self.signatures += 1
        return bytearray(self.signer.sign_digest_info(bytes))

    def _rawPublicKeyOp(self, ciphertext):
        return pow(ciphertext, self.e, self.n)

    def _rawPrivateKeyOp(self, message):
        raise NotImplementedError("The private key stays in the token; use sign().")

    def acceptsPassword(self):
        return False

    def write(self, password=None):
        raise NotImplementedError("The private key does not leave the token.")


def tls_settings():
    settings = HandshakeSettings()
    settings.minVersion = settings.maxVersion = (3, 3)
    settings.rsaSchemes = ["pkcs1"]  # what the token does with CKM_RSA_PKCS
    settings.rsaSigHashes = ["sha256", "sha384", "sha512"]
    return settings


def tlslite_chain(certs):
    parsed = []
    for cert in certs:
        item = X509()
        item.parseBinary(cert.public_bytes(Encoding.DER))  # parseBinary returns nothing
        parsed.append(item)
    return X509CertChain(parsed)


@lru_cache(maxsize=1)
def _roots():
    with warnings.catch_warnings():  # certifi still ships a few old roots with negative serials
        warnings.simplefilter("ignore")
        with open(certifi.where(), "rb") as handle:
            return Store(x509.load_pem_x509_certificates(handle.read()))


def verify_server(connection, host):
    """Check the server chain and name before any byte of the request is sent."""
    chain = [x509.load_der_x509_certificate(bytes(c.bytes)) for c in connection.session.serverCertChain.x509List]
    builder = PolicyBuilder().store(_roots()).time(datetime.now())
    builder.build_server_verifier(x509.DNSName(host)).verify(chain[0], chain[1:])


class RenegotiatingTLSConnection(TLSConnection):
    """A tlslite client that answers one server-initiated renegotiation with its client certificate."""

    def __init__(self, sock):
        super().__init__(sock)
        self.client_credentials = None  # (tlslite chain, key, settings, server name)
        self.renegotiations = 0
        self._client_verify_data = None
        self._renegotiating = False

    def _sendMsg(self, msg, randomizeFirstBlock=True, update_hashes=True):
        if isinstance(msg, Finished):
            self._client_verify_data = bytearray(msg.verify_data)
        if isinstance(msg, ClientHello) and self._renegotiating:
            # RFC 5746 §3.5: the renegotiation ClientHello carries client_verify_data and no SCSV.
            msg.cipher_suites = [c for c in msg.cipher_suites if c != CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
            extensions = [e for e in (msg.extensions or []) if e.extType != ExtensionType.renegotiation_info]
            extensions.append(RenegotiationInfoExtension().create(self._client_verify_data))
            msg.extensions = extensions
        return super()._sendMsg(msg, randomizeFirstBlock, update_hashes)

    def _getNextRecord(self):
        for result in super()._getNextRecord():
            if result in (0, 1):
                yield result
                continue
            header, parser = result
            if (
                not self._renegotiating
                and self.session is not None
                and header.type == ContentType.handshake
                and parser.bytes[:1] == bytearray([HandshakeType.hello_request])
            ):
                # Answered inline: an exception would unwind through tlslite's readAsync, which closes the socket.
                yield from self._renegotiate()
                continue
            yield result

    def _renegotiate(self):
        if self.client_credentials is None:
            raise RuntimeError("The server asked for a client certificate by renegotiation and none was given")
        if self.renegotiations:
            raise RuntimeError("The server asked to renegotiate more than once")
        chain, key, settings, server_name = self.client_credentials
        self.renegotiations += 1
        self._renegotiating = True
        self.closed = True  # tlslite starts a handshake only on a connection it considers closed
        try:
            yield from self.handshakeClientCert(chain, key, settings=settings, serverName=server_name, async_=True)
        finally:
            self._renegotiating = False


def _parse(url, data):
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    if not lines or len(lines[0].split()) < 2:
        raise RuntimeError("The server closed the connection without an HTTP response")
    status = int(lines[0].split()[1])
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers.append((name.strip(), value.strip()))
    if any(k.lower() == "transfer-encoding" and "chunked" in v.lower() for k, v in headers):
        body = _dechunk(body)
    return Response(url, status, headers, body)


def _dechunk(body):
    out, rest = b"", body
    while rest:
        size_line, _, rest = rest.partition(b"\r\n")
        size = int(size_line.split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out, rest = out + rest[:size], rest[size + 2 :]
    return out


def fetch(url, *, signer, method="GET", cookies=None, form=None, headers=None, timeout=60):
    """One request on a fresh connection (Connection: close), offering the token's certificate.

    The certificate is sent only if the server asks for it, either in the first handshake or by renegotiation.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("mTLS requests need an https URL")
    port = parts.port or 443
    host_header = parts.hostname if port == 443 else f"{parts.hostname}:{port}"
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    body = urlencode(form).encode() if form is not None else b""
    lines = [
        f"{method} {target} HTTP/1.1",
        f"Host: {host_header}",
        f"User-Agent: {USER_AGENT}",
        "Accept: text/html,application/xhtml+xml,*/*",
        "Accept-Language: pt-BR,pt;q=0.9",
        "Connection: close",
    ]
    if form is not None:
        lines += ["Content-Type: application/x-www-form-urlencoded", f"Content-Length: {len(body)}"]
    if cookies:
        lines.append("Cookie: " + "; ".join(f"{k}={v}" for k, v in cookies.items()))
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]

    chain, key, settings = tlslite_chain(signer.chain), TokenRSAKey(signer), tls_settings()
    connection = RenegotiatingTLSConnection(socket.create_connection((parts.hostname, port), timeout=timeout))
    try:
        connection.handshakeClientCert(chain, key, settings=settings, serverName=parts.hostname)
        verify_server(connection, parts.hostname)
        connection.client_credentials = (chain, key, settings, parts.hostname)
        connection.write(("\r\n".join(lines) + "\r\n\r\n").encode() + body)
        data = b""
        while len(data) < MAX_BODY:
            try:
                chunk = connection.read()
            except TLSAbruptCloseError:
                break  # Some servers drop the socket right after the response instead of sending close_notify.
            if not chunk:
                break
            data += bytes(chunk)
    finally:
        connection.close()
    response = _parse(url, data)
    response.certificate_sent = key.signatures > 0
    return response
