"""Public household endpoints must never reach private networks or leak credentials."""

import io
import json
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import Mock, patch

from amazon_echo_home_voice import gateway


NOW = 1800000000
PUBLIC_IP = "8.8.8.8"


def records(*addresses):
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET,
         socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))
        for address in addresses
    ]


def energy_body():
    return json.dumps({
        "schema_version": 1, "generated_at": NOW, "mqtt_connected": True,
        "metrics": {"battery_soc": {}, "solar_power": {}, "solar_today": {}},
        "reports": {name: {"status": "fresh", "text": "Example energy report."}
                    for name in gateway.REPORT_NAMES},
    }).encode()


class SecureSocket:
    def __init__(self, response):
        self.body = io.BytesIO(response)
        self.timeouts = []
        self.sent = b""
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv_into(self, buffer):
        return self.body.readinto(buffer)

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True


class PublicGatewayTests(unittest.TestCase):
    def setUp(self):
        self.config = gateway.GatewayConfig(
            "https://home.example/v1/energy", "example-read-token",
            "example-access-id", "example-access-secret", public_only=True,
        )

    def test_nonpublic_literals_and_ambiguous_hostnames_are_rejected(self):
        hosts = (
            "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1",
            "169.254.169.254", "100.64.0.1", "192.0.0.9", "192.88.99.1",
            "0.0.0.0", "224.0.0.1", "240.0.0.1", "198.18.0.1",
            "192.0.2.1", "198.51.100.1", "203.0.113.1", "[::1]",
            "[fe80::1]", "[fc00::1]", "[ff02::1]", "[2001:db8::1]",
            "[::ffff:8.8.8.8]", "[64:ff9b::808:808]", "[2002:808:808::1]",
            "[2001::1]", "[3fff::1]", "[fe80::1%25eth0]", "localhost",
            "home.example.", "-home.example", "home_.example", "home..example",
            "home%2eexample", "homé.example", "home.example\\other",
        )
        for host in hosts:
            with self.subTest(host=host), self.assertRaises(gateway.GatewayError):
                gateway.GatewayConfig(f"https://{host}/v1/energy", "token", public_only=True)

    def test_personal_mode_still_accepts_operator_managed_private_endpoints(self):
        config = gateway.GatewayConfig("https://192.168.0.1/v1/energy", "token")
        self.assertFalse(config.public_only)

    def test_valid_dns_and_public_literal_configuration(self):
        for host in ("home.example", "xn--bcher-kva.example", PUBLIC_IP,
                     "[2606:4700:4700::1111]"):
            with self.subTest(host=host):
                gateway.GatewayConfig(f"https://{host}/v1/energy", "token", public_only=True)

    def test_mixed_dns_answers_fail_before_creating_a_connection(self):
        for answers in (
            records(PUBLIC_IP, "127.0.0.1"), records("10.0.0.1", PUBLIC_IP),
            records("::ffff:8.8.8.8"), records("2002:808:808::1"),
            records("169.254.169.254"), records("::1"), [],
        ):
            with self.subTest(answers=answers), \
                    patch.object(socket, "getaddrinfo", return_value=answers), \
                    patch.object(gateway, "_PinnedHTTPSConnection") as connection, \
                    self.assertRaises(gateway.GatewayError):
                gateway.fetch_energy(self.config, now=NOW)
            connection.assert_not_called()

    def test_dns_failure_and_opener_override_fail_closed(self):
        with patch.object(socket, "getaddrinfo", side_effect=OSError("private details")), \
                self.assertRaises(gateway.GatewayError) as caught:
            gateway.fetch_energy(self.config, now=NOW)
        self.assertNotIn("private details", str(caught.exception))
        opener = Mock()
        with self.assertRaises(gateway.GatewayError):
            gateway.fetch_energy(self.config, opener=opener, now=NOW)
        opener.open.assert_not_called()

    def test_dns_wait_is_bounded_and_exhausted_slots_fail_fast(self):
        release = threading.Event()
        started = threading.Event()
        slots = threading.BoundedSemaphore(1)

        def stalled(*args):
            started.set()
            release.wait(2)
            return records(PUBLIC_IP)

        try:
            with patch.object(gateway, "_DNS_SLOTS", slots), \
                    patch.object(socket, "getaddrinfo", side_effect=stalled):
                before = time.monotonic()
                with self.assertRaises(gateway.GatewayError):
                    gateway._resolve_public("home.example", before + 0.02)
                self.assertTrue(started.is_set())
                self.assertLess(time.monotonic() - before, 0.5)
                with self.assertRaisesRegex(gateway.GatewayError, "busy"):
                    gateway._resolve_public("home.example", time.monotonic() + 1)
                release.set()
                # Let the daemon release its slot before restoring the module patch.
                self.assertTrue(slots.acquire(timeout=1))
                slots.release()
        finally:
            release.set()

    def test_public_ip_literal_never_invokes_dns(self):
        with patch.object(socket, "getaddrinfo") as resolver:
            value = gateway._resolve_public(PUBLIC_IP, time.monotonic() + 1)
        self.assertEqual(value, (socket.AF_INET, (PUBLIC_IP, 443)))
        resolver.assert_not_called()

    def exchange(self, response):
        secured = SecureSocket(response)
        raw = Mock()
        context = Mock()
        context.wrap_socket.return_value = secured
        with patch.object(socket, "getaddrinfo", side_effect=[records(PUBLIC_IP), records("127.0.0.1")]) as dns, \
                patch.object(socket, "socket", return_value=raw) as factory, \
                patch.object(ssl, "create_default_context", return_value=context) as create_context:
            result = gateway.fetch_energy(self.config, now=NOW)
        dns.assert_called_once_with("home.example", 443, socket.AF_UNSPEC,
                                    socket.SOCK_STREAM, socket.IPPROTO_TCP)
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        raw.connect.assert_called_once_with((PUBLIC_IP, 443))
        create_context.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(raw, server_hostname="home.example")
        self.assertTrue(secured.closed)
        self.assertIn(b"Host: home.example\r\n", secured.sent)
        self.assertIn(b"GET /v1/energy HTTP/1.1\r\n", secured.sent)
        self.assertIn(b"Authorization: Bearer example-read-token\r\n", secured.sent)
        self.assertIn(b"CF-Access-Client-Id: example-access-id\r\n", secured.sent)
        self.assertIn(b"CF-Access-Client-Secret: example-access-secret\r\n", secured.sent)
        self.assertTrue(all(0 < timeout <= 3 for timeout in secured.timeouts))
        return result

    def test_dns_is_pinned_and_tls_uses_original_hostname_for_close_responses(self):
        body = energy_body()
        response = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Connection: close\r\nContent-Length: " + str(len(body)).encode()
                    + b"\r\n\r\n" + body)
        self.assertEqual(self.exchange(response), json.loads(body))

    def test_chunked_http_response_works_with_total_deadline_reader(self):
        body = energy_body()
        response = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                    + hex(len(body))[2:].encode() + b"\r\n" + body + b"\r\n0\r\n\r\n")
        self.assertEqual(self.exchange(response), json.loads(body))

    def test_redirects_auth_failures_and_oversize_bodies_are_never_followed(self):
        for response in (
            b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/\r\nContent-Length: 0\r\n\r\n",
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 0\r\n\r\n",
            b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + b"x" * 32769,
            b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * gateway.MAX_PUBLIC_WIRE_BYTES
            + b"\r\nContent-Length: 0\r\n\r\n",
        ):
            secured = SecureSocket(response)
            context = Mock()
            context.wrap_socket.return_value = secured
            with self.subTest(response=response[:30]), \
                    patch.object(socket, "getaddrinfo", return_value=records(PUBLIC_IP)), \
                    patch.object(socket, "socket") as factory, \
                    patch.object(ssl, "create_default_context", return_value=context), \
                    self.assertRaises(gateway.GatewayError):
                gateway.fetch_energy(self.config, now=NOW)
            self.assertEqual(factory.call_count, 1)
            self.assertTrue(secured.closed)

    def test_tls_verification_is_required_and_failure_sends_no_authentication(self):
        endpoint = (socket.AF_INET, (PUBLIC_IP, 443))
        connection = gateway._PinnedHTTPSConnection("home.example", endpoint, time.monotonic() + 3)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        raw = Mock()
        context = Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError("wrong hostname")
        with patch.object(socket, "getaddrinfo", return_value=records(PUBLIC_IP)), \
                patch.object(socket, "socket", return_value=raw), \
                patch.object(ssl, "create_default_context", return_value=context), \
                self.assertRaises(gateway.GatewayError):
            gateway.fetch_energy(self.config, now=NOW)
        raw.sendall.assert_not_called()
        raw.close.assert_called_once()

    def test_read_deadline_cannot_be_extended_by_a_slow_response(self):
        sock = Mock()
        transport = gateway._DeadlineSocket(sock, 5.0)
        reader = transport.makefile("rb")
        with patch.object(gateway.time, "monotonic", return_value=5.01), \
                self.assertRaises(gateway.GatewayError):
            reader.read(1)
        sock.recv_into.assert_not_called()
        transport.close()
        sock.close.assert_not_called()
        reader.close()
        sock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
