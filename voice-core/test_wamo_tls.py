"""Tests für wamo_tls.py und die TLS-Anbindung in auth_gate.py und memory.py (ohne fremdes Netz, eigene Test-CA).

Die Handshake-Tests starten einen TLS-Server auf 127.0.0.1 mit einem Zertifikat der Test-CA (IP-SAN 127.0.0.1)
und prüfen die Kontexte aus wamo_tls mit einem echten Handshake.

Ausführen:  uv run python -m pytest -q test_wamo_tls.py   (im Ordner voice-core/)
"""

import asyncio
import datetime as dt
import ipaddress
import os
import ssl
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

import auth_gate
import memory as m
import wamo_tls

REPO_CA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-ca.crt"
TEST_CN = "WAMO Test-CA"
FREMD_CN = "Fremde Test-CA"
USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"  # neutrale Test-UUID
DSN = "postgresql://wamo_core.wamo:geheim@192.168.178.136:55322/postgres"


# ---------- Test-PKI ----------
def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _make_ca(cn: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_leaf(ca_key, ca_cert: x509.Certificate, ip: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wamo-test-server")]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _common_names(ctx: ssl.SSLContext) -> set[str]:
    names = set()
    for cert in ctx.get_ca_certs():
        for rdn in cert.get("subject", ()):
            for key, value in rdn:
                if key == "commonName":
                    names.add(value)
    return names


class _CaTest(unittest.IsolatedAsyncioTestCase):
    """Test-CA, fremde CA und kaputte Dateien; env() setzt oder entfernt WAMO_CA_FILE nur für den Block."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        self.ca_key, ca_cert = _make_ca(TEST_CN)
        self.ca_cert = ca_cert
        self.ca = str(d / "test-ca.crt")
        Path(self.ca).write_bytes(_pem(ca_cert))
        _, fremd_cert = _make_ca(FREMD_CN)
        self.fremd = str(d / "fremd-ca.crt")
        Path(self.fremd).write_bytes(_pem(fremd_cert))
        self.kaputt = str(d / "kaputt.crt")
        Path(self.kaputt).write_text("kein Zertifikat\n", encoding="utf-8")
        self.fehlt = str(d / "gibt-es-nicht.crt")

    def tearDown(self):
        self._dir.cleanup()

    @contextmanager
    def env(self, ca: str | None):
        with mock.patch.dict(os.environ):
            os.environ.pop(wamo_tls.CA_ENV, None)
            if ca is not None:
                os.environ[wamo_tls.CA_ENV] = ca
            yield


# ---------- Kontexte ----------
class TlsContextTests(_CaTest):
    def test_ohne_ca_gilt_der_standard(self):
        with self.env(None):
            self.assertIsNone(wamo_tls.https_context())
            for dsn in (DSN, f"{DSN}?sslmode=verify-full", f"{DSN}?sslmode=prefer"):
                with self.subTest(dsn=dsn):
                    self.assertIsNone(wamo_tls.postgres_ssl(dsn))

    def test_https_vertraut_wamo_ca_und_oeffentlichen_zertifikaten(self):
        with self.env(self.ca):
            ctx = wamo_tls.https_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertIn(TEST_CN, _common_names(ctx))
        self.assertGreater(len(ctx.get_ca_certs()), 1)  # certifi bleibt dabei

    def test_postgres_verify_full_vertraut_nur_der_wamo_ca(self):
        for dsn in (f"{DSN}?sslmode=verify-full", f"{DSN}?sslmode=verify_full", f"{DSN}?sslmode=VERIFY-FULL", DSN):
            with self.subTest(dsn=dsn), self.env(self.ca):
                ctx = wamo_tls.postgres_ssl(dsn)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(ctx.check_hostname)  # ohne sslmode gilt verify-full
                self.assertEqual(_common_names(ctx), {TEST_CN})
                self.assertEqual(len(ctx.get_ca_certs()), 1)
                self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_postgres_verify_ca_ohne_hostnamen(self):
        for dsn in (f"{DSN}?sslmode=verify-ca", f"{DSN}?sslmode=verify_ca"):
            with self.subTest(dsn=dsn), self.env(self.ca):
                ctx = wamo_tls.postgres_ssl(dsn)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertFalse(ctx.check_hostname)

    def test_postgres_schwacher_sslmode_mit_ca_ist_fehler(self):
        with self.env(self.ca):
            for mode in ("disable", "allow", "prefer", "require", "unsinn"):
                with self.subTest(mode=mode), self.assertRaises(wamo_tls.TlsConfigError):
                    wamo_tls.postgres_ssl(f"{DSN}?sslmode={mode}")
            # letzter Wert gilt wie bei asyncpg
            with self.assertRaises(wamo_tls.TlsConfigError):
                wamo_tls.postgres_ssl(f"{DSN}?sslmode=verify-full&sslmode=require")

    def test_postgres_eigenes_sslrootcert_hat_vorrang(self):
        with self.env(self.ca):
            self.assertIsNone(wamo_tls.postgres_ssl(f"{DSN}?sslmode=verify-full&sslrootcert=/etc/ssl/eigene.crt"))

    def test_postgres_ohne_dsn(self):
        with self.env(self.ca):
            self.assertIsNone(wamo_tls.postgres_ssl(None))
            self.assertIsNone(wamo_tls.postgres_ssl(""))

    def test_sslmode_normalisiert_und_letzter_wert_gilt(self):
        self.assertEqual(wamo_tls.sslmode(f"{DSN}?sslmode=require&sslmode=verify_full"), "verify-full")
        self.assertEqual(wamo_tls.sslmode(f"{DSN}?sslmode=Verify-CA"), "verify-ca")
        self.assertIsNone(wamo_tls.sslmode(DSN))

    def test_unlesbare_ca_ist_ein_fehler(self):
        for path in (self.fehlt, self.kaputt):
            with self.subTest(path=path), self.env(path):
                with self.assertRaises(wamo_tls.CaFileError):
                    wamo_tls.https_context()
                with self.assertRaises(wamo_tls.CaFileError):
                    wamo_tls.postgres_ssl(f"{DSN}?sslmode=verify-full")
        self.assertTrue(issubclass(wamo_tls.CaFileError, wamo_tls.TlsConfigError))

    def test_anfuehrungszeichen_und_leerzeichen_werden_entfernt(self):
        with self.env(f'  "{self.ca}" '):
            self.assertEqual(wamo_tls.ca_file(), self.ca)
        with self.env("   "):
            self.assertIsNone(wamo_tls.ca_file())


# ---------- echte Handshakes gegen einen TLS-Server auf 127.0.0.1 ----------
class HandshakeTests(_CaTest):
    async def asyncSetUp(self):
        key, leaf = _make_leaf(self.ca_key, self.ca_cert, "127.0.0.1")
        d = Path(self._dir.name)
        (d / "server.crt").write_bytes(_pem(leaf))
        (d / "server.key").write_bytes(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(d / "server.crt", d / "server.key")

        async def antwort(reader, writer):
            try:
                writer.write(b"ok")
                await writer.drain()
            except (ConnectionError, ssl.SSLError):
                pass
            finally:
                writer.close()

        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: None)  # abgelehnte Handshakes nicht ins Log
        self.server = await asyncio.start_server(antwort, "127.0.0.1", 0, ssl=server_ctx)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def handshake(self, ctx: ssl.SSLContext, server_hostname: str) -> bytes:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", self.port, ssl=ctx, server_hostname=server_hostname), 5
        )
        try:
            return await asyncio.wait_for(reader.read(2), 5)
        finally:
            writer.close()

    def postgres_ctx(self, ca: str, mode: str) -> ssl.SSLContext:
        with self.env(ca):
            return wamo_tls.postgres_ssl(f"postgresql://u:p@127.0.0.1:5432/postgres?sslmode={mode}")

    def https_ctx(self, ca: str) -> ssl.SSLContext:
        with self.env(ca):
            return wamo_tls.https_context()

    async def test_postgres_verify_full_mit_richtiger_ip(self):
        self.assertEqual(await self.handshake(self.postgres_ctx(self.ca, "verify-full"), "127.0.0.1"), b"ok")

    async def test_postgres_verify_full_falscher_name_scheitert(self):
        for name in ("127.0.0.2", "falsch.example"):
            with self.subTest(name=name), self.assertRaises(ssl.SSLCertVerificationError):
                await self.handshake(self.postgres_ctx(self.ca, "verify-full"), name)

    async def test_postgres_verify_ca_trotz_falschem_namen(self):
        self.assertEqual(await self.handshake(self.postgres_ctx(self.ca, "verify-ca"), "falsch.example"), b"ok")

    async def test_postgres_fremde_ca_scheitert(self):
        for mode in ("verify-full", "verify-ca"):
            with self.subTest(mode=mode), self.assertRaises(ssl.SSLCertVerificationError):
                await self.handshake(self.postgres_ctx(self.fremd, mode), "127.0.0.1")

    async def test_https_mit_richtiger_ip(self):
        self.assertEqual(await self.handshake(self.https_ctx(self.ca), "127.0.0.1"), b"ok")

    async def test_https_falscher_name_scheitert(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await self.handshake(self.https_ctx(self.ca), "falsch.example")

    async def test_https_fremde_ca_scheitert(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await self.handshake(self.https_ctx(self.fremd), "127.0.0.1")


# ---------- memory.Database ----------
class _FakePool:
    def is_closing(self):
        return False


class DatabaseTlsTests(_CaTest):
    async def _pool_kwargs(self, dsn: str) -> dict:
        captured = {}

        async def fake_create_pool(**kwargs):
            captured.update(kwargs)
            return _FakePool()

        with mock.patch.object(m.asyncpg, "create_pool", fake_create_pool):
            await m.Database(dsn, "wamo_test").pool()
        return captured

    async def test_mit_ca_bekommt_asyncpg_den_wamo_kontext(self):
        for dsn in (f"{DSN}?sslmode=verify-full", DSN):  # ohne sslmode gilt verify-full
            with self.subTest(dsn=dsn), self.env(self.ca):
                kwargs = await self._pool_kwargs(dsn)
                self.assertIsInstance(kwargs["ssl"], ssl.SSLContext)
                self.assertTrue(kwargs["ssl"].check_hostname)
                self.assertEqual(_common_names(kwargs["ssl"]), {TEST_CN})

    async def test_ohne_ca_entscheidet_die_dsn(self):
        with self.env(None):
            kwargs = await self._pool_kwargs(DSN)
        self.assertNotIn("ssl", kwargs)

    async def test_schwacher_sslmode_mit_ca_sperrt_die_datenbank(self):
        for mode in ("disable", "prefer", "require"):
            with self.subTest(mode=mode), self.env(self.ca), self.assertRaises(m.DatabaseUnavailable):
                await self._pool_kwargs(f"{DSN}?sslmode={mode}")

    async def test_unlesbare_ca_sperrt_die_datenbank(self):
        with self.env(self.fehlt):
            with self.assertRaises(m.DatabaseUnavailable):
                await self._pool_kwargs(f"{DSN}?sslmode=verify-full")


# ---------- auth_gate.TokenVerifier ----------
class TokenVerifierTlsTests(_CaTest):
    URL = "https://192.168.178.136:55443"

    def test_jwks_abruf_vertraut_der_wamo_ca(self):
        with self.env(self.ca):
            verifier = auth_gate.TokenVerifier(self.URL, "sb_publishable_test")
        self.assertIn(TEST_CN, _common_names(verifier.ssl_context))
        self.assertIs(verifier._jwks.ssl_context, verifier.ssl_context)

    def test_ohne_ca_standard_der_bibliotheken(self):
        with self.env(None):
            verifier = auth_gate.TokenVerifier(self.URL)
        self.assertIsNone(verifier.ssl_context)
        self.assertIsNone(verifier._jwks.ssl_context)

    def test_ohne_url_wird_die_ca_nicht_gelesen(self):
        with self.env(self.fehlt):
            self.assertIsNone(auth_gate.TokenVerifier(None).ssl_context)

    def test_unlesbare_ca_verhindert_den_start(self):
        with self.env(self.fehlt):
            with self.assertRaises(wamo_tls.CaFileError):
                auth_gate.TokenVerifier(self.URL)

    async def _gotrue_verify(self, ca: str | None):
        now = int(time.time())
        token = jwt.encode(
            {"sub": USER, "aal": "aal2", "aud": "authenticated", "iss": f"{self.URL}/auth/v1", "iat": now,
             "exp": now + 600},
            "nur-ein-testgeheimnis-fuer-hs256-tokens",
            algorithm="HS256",
        )
        seen = {}
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": USER}))

        def client(**kwargs):
            seen.update(kwargs)
            return real_client(**{**kwargs, "transport": transport})

        with self.env(ca):
            verifier = auth_gate.TokenVerifier(self.URL, "sb_publishable_test")
        with mock.patch.object(auth_gate.httpx, "AsyncClient", client):
            result = await verifier.check(token)
        self.assertEqual(result.user_id, USER)
        return verifier, seen["verify"]

    async def test_gotrue_rueckfall_nutzt_denselben_kontext(self):
        verifier, verify = await self._gotrue_verify(self.ca)
        self.assertIs(verify, verifier.ssl_context)

    async def test_gotrue_rueckfall_ohne_ca_prueft_mit_standard(self):
        _, verify = await self._gotrue_verify(None)
        self.assertIs(verify, True)


# ---------- Root-Zertifikat im Repo ----------
@unittest.skipUnless(REPO_CA.is_file(), "infra/sokrates2/wamo-ca.crt fehlt")
class RepoCaTests(unittest.TestCase):
    """Die Datei im Repo ist das öffentliche Root-Zertifikat der WAMO-CA, nie ein Schlüssel."""

    def test_nur_ein_oeffentliches_ca_zertifikat(self):
        text = REPO_CA.read_text(encoding="ascii")
        self.assertEqual(text.count("-----BEGIN CERTIFICATE-----"), 1)
        self.assertNotIn("PRIVATE KEY", text)
        cert = x509.load_pem_x509_certificate(text.encode("ascii"))
        self.assertEqual(cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value, "WAMO interne CA")
        self.assertEqual(cert.subject, cert.issuer)  # Root, selbst signiert
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        self.assertTrue(basic.critical and basic.value.ca)
        self.assertGreater(cert.not_valid_after_utc, dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365))

    def test_ca_ist_auf_wamo_adressen_begrenzt(self):
        cert = x509.load_pem_x509_certificate(REPO_CA.read_bytes())
        nc = cert.extensions.get_extension_for_class(x509.NameConstraints)
        self.assertTrue(nc.critical)
        self.assertIsNone(nc.value.excluded_subtrees)
        self.assertEqual(
            set(nc.value.permitted_subtrees),
            {
                x509.IPAddress(ipaddress.ip_network("192.168.178.136/32")),
                x509.IPAddress(ipaddress.ip_network("127.0.0.1/32")),
                x509.DNSName("sokrates2"),
                x509.DNSName("spark-651f.local"),
                x509.DNSName("localhost"),
                x509.DNSName("supavisor"),
                x509.DNSName("wamo-supabase-pooler"),
            },
        )

    def test_ca_nur_fuer_server_und_hoechstens_eine_zwischenstufe(self):
        cert = x509.load_pem_x509_certificate(REPO_CA.read_bytes())
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        self.assertEqual(list(eku), [ExtendedKeyUsageOID.SERVER_AUTH])
        self.assertEqual(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length, 1)
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage)
        self.assertTrue(usage.critical and usage.value.key_cert_sign and usage.value.crl_sign)
        self.assertFalse(usage.value.digital_signature)

    def test_ca_ist_als_vertrauensanker_ladbar(self):
        with mock.patch.dict(os.environ, {wamo_tls.CA_ENV: str(REPO_CA)}):
            ctx = wamo_tls.postgres_ssl(f"{DSN}?sslmode=verify-full")
        self.assertEqual(_common_names(ctx), {"WAMO interne CA"})


if __name__ == "__main__":
    unittest.main()
