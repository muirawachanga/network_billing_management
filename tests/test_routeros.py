import ssl
import unittest
from collections import deque
from unittest.mock import patch

from network_billing_system import routeros


POLICY = {
    "host": "router.invalid", "port": 8728, "transport": "Plaintext", "allow_plaintext": True,
    "api_user": "api-user", "api_password": "api=secret", "server": "guest",
    "profile": "paid", "rate_limit": "2M/8M", "router_identity": "test-router",
}
USER = {
    ".id": "*A", "name": "guest-1", "comment": "checkout-1", "server": "guest",
    "disabled": "false", "profile": "paid", "password": "customer=pw",
    "limit-uptime": "1h", "uptime": "3m",
}
ACTIVE = {
    ".id": "*B", "user": "guest-1", "server": "guest",
    "uptime": "1m", "session-time-left": "56m",
}


def sentence(*words):
    def frame(word):
        data = word.encode("utf-8")
        size = len(data)
        return (bytes([size]) if size < 128 else
                (size | 0x8000).to_bytes(2, "big")) + data
    return b"".join(map(frame, words)) + b"\0"


def reply(*rows, done=()):
    return b"".join(
        sentence("!re", *("=%s=%s" % item for item in row.items())) for row in rows
    ) + sentence("!done", *done)


class ScriptedSocket:
    def __init__(self, case, script):
        self.case = case
        self.script = deque(script)
        self.pending = b""
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        self.case.assertGreater(timeout, 0)

    def sendall(self, data):
        self.case.assertFalse(self.pending)
        self.case.assertTrue(self.script, f"Unexpected command: {data!r}")
        expected, self.pending = self.script.popleft()
        wire = sentence(*expected)
        if len(expected) > 1:
            self.case.assertEqual(data, wire)
        else:
            self.case.assertTrue(data.startswith(wire[:-1]), (data, expected))
        self.sent.append(data)

    def recv(self, count):
        data, self.pending = self.pending[:min(2, count)], self.pending[min(2, count):]
        return data

    def close(self):
        self.closed = True


def lookup(path, *rows):
    return (path + "/print",), reply(*rows)


def configuration(*, rate="2M/8M", methods="cookie,http-chap", timeout="0s"):
    return [
        lookup("/ip/hotspot", {"name": "guest", "disabled": "false", "profile": "gp"}),
        lookup("/ip/hotspot/profile", {"name": "gp", "login-by": methods, "use-radius": "false"}),
        lookup("/ip/hotspot/user/profile", {
            "name": "paid", "shared-users": "1", "rate-limit": rate, "session-timeout": timeout,
        }),
    ]


class TestRouterOS(unittest.TestCase):
    def mock_connection(self, script, *, identity="test-router", login=None):
        sock = ScriptedSocket(self, [
            (("/login", "=name=api-user", "=password=api=secret"),
             reply() if login is None else login),
            *([lookup("/system/identity", {"name": identity})] if login is None else []),
            *script,
        ])
        opener = patch.object(routeros, "_open", return_value=sock)
        opener.start()
        self.addCleanup(opener.stop)
        self.addCleanup(lambda: self.assertEqual(list(sock.script), []))
        return sock

    def test_health_and_close(self):
        sock = self.mock_connection([lookup("/system/resource", {"uptime": "1w2d03:04:05"})])
        with routeros.connect(POLICY) as session:
            self.assertEqual(session.health(), 788645)
        self.assertTrue(sock.closed)
        with self.assertRaises(routeros.RouterError):
            session.health()

    def test_health_only_policy_cannot_manage_accounts(self):
        self.mock_connection([lookup("/system/resource", {"uptime": "4s"})])
        policy = {k: v for k, v in POLICY.items() if k not in ("profile", "rate_limit")}
        with routeros.connect(policy) as session:
            self.assertEqual(session.health(), 4)
            for method, args in (
                (session.ensure_user, ("guest-1", "p", "checkout-1", 5)),
                (session.account, ("guest-1", "checkout-1")),
                (session.revoke_user, ("guest-1", "checkout-1")),
            ):
                with self.assertRaisesRegex(routeros.RouterError, "profile and rate limit"):
                    method(*args)

    def test_wrong_router_never_yields_a_session(self):
        sock = self.mock_connection([], identity="untrusted-router")
        with self.assertRaisesRegex(routeros.RouterError, "identity") as error:
            with routeros.connect(POLICY):
                self.fail("wrong router accepted")
        self.assertNotIn("untrusted-router", str(error.exception))
        self.assertTrue(sock.closed)

    def test_trap_is_redacted_and_connection_closed(self):
        sock = self.mock_connection([], login=sentence("!trap", "=message=api=secret"))
        with self.assertRaises(routeros.RouterError) as error:
            with routeros.connect(POLICY):
                self.fail("login trap accepted")
        self.assertNotIn("api=secret", str(error.exception))
        self.assertTrue(sock.closed)

    def test_transport_must_be_explicit(self):
        for omitted in ("transport", "allow_plaintext"):
            policy = dict(POLICY)
            del policy[omitted]
            with self.subTest(omitted=omitted), patch.object(routeros, "_open") as opener:
                with self.assertRaises(routeros.RouterError):
                    with routeros.connect(policy):
                        self.fail("non-opted-in transport accepted")
                opener.assert_not_called()

    def test_verified_tls_has_no_plaintext_fallback(self):
        with patch.object(routeros, "_addresses", return_value=[
            (2, 1, 6, "", ("192.0.2.1", 8729)),
        ]), patch.object(routeros.socket, "socket") as socket_factory, patch.object(
            ssl.SSLContext, "wrap_socket", autospec=True,
            side_effect=ssl.SSLCertVerificationError("certificate or hostname mismatch"),
        ) as wrap:
            with self.assertRaisesRegex(routeros.RouterError, "verification"):
                with routeros.connect(dict(POLICY, transport="TLS", port=8729)):
                    self.fail("unverified TLS accepted")
            context, raw = wrap.call_args.args
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(wrap.call_args.kwargs["server_hostname"], POLICY["host"])
            raw.sendall.assert_not_called()
            raw.close.assert_called_once()
            socket_factory.assert_called_once()

    def test_rejects_oversized_and_truncated_frames(self):
        for malformed in (b"\xf0\x00\x10\x00\x00", b"\x03!r"):
            with self.subTest(malformed=malformed):
                sock = ScriptedSocket(self, [(("/login",), malformed)])
                with patch.object(routeros, "_open", return_value=sock):
                    with self.assertRaises(routeros.RouterError):
                        with routeros.connect(POLICY):
                            self.fail("bad login frame accepted")
                self.assertTrue(sock.closed)

    def test_caps_aggregate_response_even_when_every_word_fits(self):
        payload = sentence("!re", "=uptime=" + "x" * 201)
        sock = self.mock_connection([(("/system/resource/print",), payload * 3 + reply())])
        with routeros.connect(POLICY) as session, patch.object(routeros, "_MAX_RESPONSE", 512):
            with self.assertRaisesRegex(routeros.RouterError, "response exceeded"):
                session.health()
        self.assertTrue(sock.closed)

    def test_fragmented_replies_do_not_extend_deadline(self):
        sock = self.mock_connection([lookup("/system/resource", {"uptime": "1h"})])
        with routeros.connect(POLICY) as session:
            with patch.object(routeros.time, "monotonic", side_effect=[0, 1, 2, 3, 15]):
                with self.assertRaisesRegex(routeros.RouterError, "deadline"):
                    session.health()
            self.assertTrue(sock.closed)

    def test_ensure_stages_enables_and_does_not_rewrite_matching_user(self):
        disabled = dict(USER, disabled="true", uptime="0s")
        sock = self.mock_connection([
            *configuration(), lookup("/ip/hotspot/user"), lookup("/ip/hotspot/active"),
            lookup("/ip/hotspot/cookie"),
            (("/ip/hotspot/user/add", "=name=guest-1", "=server=guest",
              "=comment=checkout-1", "=disabled=yes", "=password=customer=pw",
              "=limit-uptime=3600s", "=profile=paid"), reply(done=("=ret=*A",))),
            lookup("/ip/hotspot/user", disabled),
            (("/ip/hotspot/user/set", "=.id=*A", "=disabled=no"), reply()),
            lookup("/ip/hotspot/user", USER),
            *configuration(), lookup("/ip/hotspot/user", USER),
        ])
        with routeros.connect(POLICY) as session:
            self.assertIsNone(session.ensure_user("guest-1", "customer=pw", "checkout-1", 3600))
            self.assertIsNone(session.ensure_user("guest-1", "customer=pw", "checkout-1", 3600))
        self.assertEqual(sum(data.endswith(sentence("?name=guest-1")) for data in sock.sent), 4)

    def test_ensure_rejects_policy_drift_and_unowned_rows_before_writes(self):
        for script in (configuration(rate="9M/9M"), configuration(methods="mac,http-chap")[:2],
                       [*configuration(), lookup("/ip/hotspot/user", dict(USER, comment="other"))]):
            with self.subTest(script=script):
                with patch.object(routeros, "_open", return_value=ScriptedSocket(self, [
                    (("/login",), reply()), lookup("/system/identity", {"name": "test-router"}),
                    *script,
                ])):
                    with routeros.connect(POLICY) as session, self.assertRaises(routeros.RouterError):
                        session.ensure_user("guest-1", "p", "checkout-1", 3600)

    def test_revoke_cleans_up_and_retains_counter_for_retries(self):
        disabled = dict(USER, disabled="true", uptime="4m")
        no_sessions = (("/ip/hotspot/active/print",), sentence("!empty") + reply())
        self.mock_connection([
            lookup("/ip/hotspot/user", USER),
            (("/ip/hotspot/user/set", "=.id=*A", "=disabled=yes"), reply()),
            lookup("/ip/hotspot/user", disabled),
            lookup("/ip/hotspot/active", ACTIVE),
            (("/ip/hotspot/active/remove", "=.id=*B"), reply()),
            lookup("/ip/hotspot/cookie", {".id": "*C", "user": "guest-1"}),
            (("/ip/hotspot/cookie/remove", "=.id=*C"), reply()),
            no_sessions, lookup("/ip/hotspot/cookie"), lookup("/ip/hotspot/user", disabled),
            lookup("/ip/hotspot/user", disabled), lookup("/ip/hotspot/user", disabled),
            no_sessions, lookup("/ip/hotspot/cookie"),
            no_sessions, lookup("/ip/hotspot/cookie"), lookup("/ip/hotspot/user", disabled),
            *configuration(), no_sessions, lookup("/ip/hotspot/user", disabled), no_sessions,
        ])
        with routeros.connect(POLICY) as session:
            self.assertIsNone(session.revoke_user("guest-1", "checkout-1"))
            self.assertIsNone(session.revoke_user("guest-1", "checkout-1"))
            self.assertEqual(session.account("guest-1", "checkout-1"),
                             {"used_seconds": 240, "active": False})

    def test_revoke_reports_a_session_that_remains_after_removal(self):
        disabled = dict(USER, disabled="true")
        self.mock_connection([
            lookup("/ip/hotspot/user", disabled), lookup("/ip/hotspot/user", disabled),
            lookup("/ip/hotspot/active", ACTIVE),
            (("/ip/hotspot/active/remove", "=.id=*B"), reply()),
            lookup("/ip/hotspot/cookie"), lookup("/ip/hotspot/active", ACTIVE),
        ])
        with routeros.connect(POLICY) as session, self.assertRaisesRegex(
            routeros.RouterError, "revocation"
        ):
            session.revoke_user("guest-1", "checkout-1")

    def test_active_usage_is_cumulative_and_unstable_samples_are_refused(self):
        def sample(*rows, timeout="0s"):
            return [
                *configuration(timeout=timeout), lookup("/ip/hotspot/active", ACTIVE),
                lookup("/ip/hotspot/user", USER), lookup("/ip/hotspot/active", *rows),
            ]

        self.mock_connection([
            *sample(dict(ACTIVE, uptime="1m1s", **{"session-time-left": "55m59s"})),
            *sample(),
            *sample(ACTIVE, timeout="2m"),
            *sample(dict(ACTIVE, **{"session-time-left": "30m"})),
        ])
        with routeros.connect(POLICY) as session:
            self.assertEqual(session.account("guest-1", "checkout-1"),
                             {"used_seconds": 241, "active": True})
            for _ in range(3):
                with self.assertRaises(routeros.RouterError):
                    session.account("guest-1", "checkout-1")
