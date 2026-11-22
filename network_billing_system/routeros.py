"""Synchronous RouterOS binary API client for local HotSpot accounts."""

import queue
import re
import socket
import ssl
import threading
import time
from contextlib import contextmanager


class RouterError(Exception):
    """The requested result could not be verified; a write may have taken effect."""


_CONNECT_TIMEOUT = 8
_OPERATION_TIMEOUT = 12
_MAX_WORD = 16 * 1024
_MAX_RESPONSE = 256 * 1024
_MAX_SENTENCES = 512
_MAX_WORDS = 128
_DNS_SLOTS = threading.BoundedSemaphore(4)

_USER = "/ip/hotspot/user"
_ACTIVE = "/ip/hotspot/active"
_COOKIE = "/ip/hotspot/cookie"
_USER_FIELDS = "name,server,profile,comment,disabled,dynamic,uptime,limit-uptime"
_PASSWORD_LOGINS = {"http-chap", "http-pap", "https"}
_LOGIN_METHODS = _PASSWORD_LOGINS | {"cookie", "mac-cookie"}
_DURATION = re.compile(
    r"(?:(\d{1,10})w)?(?:(\d{1,10})d)?"
    r"(?:(\d{1,10})h)?(?:(\d{1,10})m)?(?:(\d{1,10})s)?", re.ASCII
)
_CLOCK = re.compile(
    r"(?:(\d{1,10})w)?(?:(\d{1,10})d)?"
    r"(\d{1,10}):([0-5]\d):([0-5]\d)", re.ASCII
)


def _seconds(value):
    if value == "0":
        return 0
    if isinstance(value, str) and value:
        matched = _DURATION.fullmatch(value) or _CLOCK.fullmatch(value)
        if matched:
            return sum(int(v or 0) * factor for v, factor in
                       zip(matched.groups(), (604800, 86400, 3600, 60, 1)))
    raise RouterError("RouterOS did not return a supported duration.")


def _text(value, message):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                   for c in value)):
        raise RouterError(message)
    return value


def _policy(policy):
    if not isinstance(policy, dict):
        raise RouterError("Invalid RouterOS policy.")
    port = policy.get("port")
    transport = policy.get("transport")
    if type(port) is not int or not 1 <= port <= 65535:
        raise RouterError("Invalid RouterOS port.")
    if transport not in ("TLS", "Plaintext"):
        raise RouterError("Select TLS or explicitly select Plaintext.")
    if transport == "Plaintext" and policy.get("allow_plaintext") is not True:
        raise RouterError("Plaintext RouterOS API access requires explicit opt-in.")
    checked = {"port": port, "transport": transport}
    for key in ("host", "api_user", "api_password", "server", "router_identity"):
        checked[key] = _text(policy.get(key), "Invalid RouterOS " + key + ".")
    if "profile" in policy or "rate_limit" in policy:
        for key in ("profile", "rate_limit"):
            checked[key] = _text(policy.get(key), "Invalid RouterOS " + key + ".")
    if checked["server"] == "all":
        raise RouterError("Select a specific HotSpot server.")
    return checked


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RouterError("RouterOS deadline exceeded.")
    return remaining


def _addresses(host, port, deadline):
    # getaddrinfo has no Python timeout; cap outstanding calls even if DNS stalls.
    if not _DNS_SLOTS.acquire(timeout=_remaining(deadline)):
        raise RouterError("RouterOS name resolution timed out.")
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError, ValueError):
            result.put(())
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=resolve, daemon=True).start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise RouterError("RouterOS name resolution unavailable.") from None
    try:
        addresses = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise RouterError("RouterOS name resolution timed out.") from None
    if not addresses:
        raise RouterError("RouterOS name resolution failed.")
    return addresses


def _close(sock):
    try:
        sock.close()
    except OSError:
        pass


def _open(policy, deadline):
    host = policy["host"]
    addresses = _addresses(host, policy["port"], deadline)
    for family, kind, proto, _, address in addresses:
        sock = None
        try:
            timeout = _remaining(deadline)
            sock = socket.socket(family, kind, proto)
            sock.settimeout(timeout)
            sock.connect(address)
        except OSError:
            if sock is not None:
                _close(sock)
            continue
        if policy["transport"] == "Plaintext":
            return sock
        try:
            context = ssl.create_default_context()
            sock.settimeout(_remaining(deadline))
            return context.wrap_socket(sock, server_hostname=host)
        except (OSError, ValueError, RouterError):
            _close(sock)
            raise RouterError("RouterOS TLS connection or verification failed.") from None
    raise RouterError("Could not connect to RouterOS.")


def _encode(word):
    data = word.encode("utf-8")
    size = len(data)
    if not 0 < size <= _MAX_WORD:
        raise RouterError("Invalid RouterOS request word size.")
    if size < 0x80:
        prefix = size.to_bytes(1, "big")
    elif size < 0x4000:
        prefix = (size | 0x8000).to_bytes(2, "big")
    else:
        prefix = (size | 0xC00000).to_bytes(3, "big")
    return prefix + data


class _Response:
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.budget = _MAX_RESPONSE

    def read(self, count):
        self.budget -= count
        if self.budget < 0:
            raise RouterError("RouterOS response exceeded its size limit.")
        data = bytearray()
        while len(data) < count:
            self.sock.settimeout(_remaining(self.deadline))
            part = self.sock.recv(count - len(data))
            if not part:
                raise RouterError("RouterOS closed an incomplete response.")
            data.extend(part)
        return data

    def word(self):
        lead = self.read(1)[0]
        if lead < 0x80:
            size = lead
        elif lead < 0xC0:
            size = ((lead & 0x3F) << 8) | self.read(1)[0]
        elif lead < 0xE0:
            size = ((lead & 0x1F) << 16) | int.from_bytes(self.read(2), "big")
        elif lead < 0xF0:
            size = ((lead & 0x0F) << 24) | int.from_bytes(self.read(3), "big")
        elif lead == 0xF0:
            size = int.from_bytes(self.read(4), "big")
        else:
            raise RouterError("Unsupported RouterOS protocol control byte.")
        if size > _MAX_WORD:
            raise RouterError("RouterOS response word exceeded its size limit.")
        return self.read(size).decode("utf-8")

    def sentence(self):
        kind = self.word()
        if kind not in ("!re", "!done", "!empty", "!trap", "!fatal"):
            raise RouterError("Invalid RouterOS reply type.")
        fields = {}
        for _ in range(_MAX_WORDS):
            word = self.word()
            if not word:
                return kind, fields
            key, sep, value = word[1:].partition("=")
            if not word.startswith("=") or not sep or not key or key in fields:
                raise RouterError("Invalid RouterOS reply attribute.")
            fields[key] = value
        raise RouterError("RouterOS response contained too many words.")


def _id(row):
    value = row.get(".id")
    if not isinstance(value, str) or not re.fullmatch(r"\*[0-9A-Fa-f]{1,16}", value):
        raise RouterError("RouterOS did not return a valid row ID.")
    return value


def _one(rows):
    if len(rows) != 1:
        raise RouterError("Could not identify a unique RouterOS row.")
    return rows[0]


class _Session:
    def __init__(self, sock, policy):
        self._sock = sock
        self._server = policy["server"]
        self._profile = policy.get("profile")
        self._rate_limit = policy.get("rate_limit")

    def close(self):
        if self._sock is not None:
            _close(self._sock)
            self._sock = None

    def _command(self, deadline, path, attrs=(), queries=()):
        if self._sock is None:
            raise RouterError("RouterOS session is closed.")
        words = [path]
        words.extend("=" + key + "=" + value for key, value in attrs)
        words.extend("?" + key + "=" + value for key, value in queries)
        request = b"".join(_encode(word) for word in words) + b"\0"
        try:
            self._sock.settimeout(_remaining(deadline))
            self._sock.sendall(request)
            response = _Response(self._sock, deadline)
            rows = []
            empty = False
            for _ in range(_MAX_SENTENCES):
                kind, fields = response.sentence()
                if kind == "!done":
                    return rows, fields
                if kind in ("!trap", "!fatal"):
                    raise RouterError("RouterOS rejected the API request.")
                if kind == "!re" and not empty:
                    rows.append(fields)
                elif kind == "!empty" and not fields and not rows and not empty:
                    empty = True
                else:
                    raise RouterError("Invalid RouterOS reply sequence.")
            raise RouterError("RouterOS response contained too many sentences.")
        except (OSError, UnicodeError):
            self.close()
            raise RouterError("RouterOS transport or encoding error.") from None
        except RouterError:
            self.close()
            raise

    def _print(self, deadline, path, fields, **query):
        rows, done = self._command(
            deadline, path + "/print", ((".proplist", fields),), query.items()
        )
        if done or any(row.get(k) != v for row in rows for k, v in query.items()):
            raise RouterError("RouterOS returned unexpected query results.")
        return rows

    def _write(self, deadline, path, **attrs):
        rows, done = self._command(deadline, path, attrs.items())
        if rows or (done and not (path == _USER + "/add" and set(done) == {"ret"})):
            raise RouterError("RouterOS returned an unexpected write result.")

    def _verify_policy(self, deadline):
        self._require_hotspot_policy()
        server = _one(self._print(
            deadline, "/ip/hotspot", "name,disabled,invalid,profile", name=self._server
        ))
        if server.get("disabled") != "false" or server.get("invalid", "false") != "false":
            raise RouterError("HotSpot server is not enabled and valid.")
        server_profile = _text(server.get("profile"), "Missing HotSpot server profile.")
        config = _one(self._print(
            deadline, "/ip/hotspot/profile", "name,login-by,use-radius", name=server_profile
        ))
        methods = set(config.get("login-by", "").split(","))
        if (config.get("use-radius") not in ("false", "no") or not methods <= _LOGIN_METHODS
                or not methods & _PASSWORD_LOGINS):
            raise RouterError("HotSpot must support local password authentication.")
        profile = _one(self._print(
            deadline, _USER + "/profile",
            "name,shared-users,rate-limit,session-timeout,on-login,on-logout",
            name=self._profile,
        ))
        if (profile.get("shared-users") != "1" or profile.get("rate-limit") != self._rate_limit
                or profile.get("on-login") or profile.get("on-logout")):
            raise RouterError("HotSpot user profile does not match the billing policy.")
        return profile

    def _require_hotspot_policy(self):
        if self._profile is None:
            raise RouterError("HotSpot operations require a profile and rate limit.")

    def _user(self, deadline, username, owner, *, optional=False, password=False):
        fields = ".id," + _USER_FIELDS + (",password" if password else "")
        rows = self._print(deadline, _USER, fields, name=username)
        if not rows and optional:
            return None
        row = _one(rows)
        _id(row)
        if (row.get("comment") != owner or row.get("server") != self._server
                or row.get("dynamic", "false") != "false"
                or row.get("disabled") not in ("true", "false")):
            raise RouterError("HotSpot user ownership, scope, or state could not be verified.")
        return row

    def _active(self, deadline, username):
        rows = self._print(
            deadline, _ACTIVE, ".id,user,server,uptime,session-time-left,radius", user=username
        )
        if (len({_id(row) for row in rows}) != len(rows)
                or any(row.get("server") != self._server for row in rows)):
            raise RouterError("HotSpot sessions could not be safely attributed.")
        return rows

    def _cookies(self, deadline, username):
        rows = self._print(deadline, _COOKIE, ".id,user", user=username)
        if len({_id(row) for row in rows}) != len(rows):
            raise RouterError("HotSpot cookies could not be safely attributed.")
        return rows

    def health(self):
        """Return router uptime in whole seconds."""
        deadline = time.monotonic() + _OPERATION_TIMEOUT
        row = _one(self._print(deadline, "/system/resource", "uptime"))
        return _seconds(row.get("uptime"))

    def ensure_user(self, username, password, owner, seconds):
        """Ensure an owned local user has the absolute cumulative time allowance."""
        _text(username, "Invalid HotSpot username.")
        _text(password, "Invalid HotSpot password.")
        _text(owner, "Invalid HotSpot owner marker.")
        if type(seconds) is not int or not 0 < seconds <= 2**31 - 1:
            raise RouterError("Invalid HotSpot uptime allowance.")
        deadline = time.monotonic() + _OPERATION_TIMEOUT
        self._verify_policy(deadline)
        row = self._user(deadline, username, owner, optional=True, password=True)
        if row and self._matches(row, password, seconds) and row["disabled"] == "false":
            return
        if self._active(deadline, username):
            raise RouterError("Cannot change a HotSpot user with an active session.")
        desired = {
            "password": password, "limit-uptime": str(seconds) + "s",
            "profile": self._profile,
        }
        if row is None:
            if self._cookies(deadline, username):
                raise RouterError("Cannot create a HotSpot user with unattributed cookies.")
            self._write(deadline, _USER + "/add", name=username, server=self._server,
                        comment=owner, disabled="yes", **desired)
        else:
            if row["disabled"] == "false":
                self._write(deadline, _USER + "/set", **{".id": _id(row), "disabled": "yes"})
                self._confirm_disabled(deadline, username, owner, _id(row))
                if self._active(deadline, username):
                    raise RouterError("Cannot change a HotSpot user with an active session.")
            if not self._matches(row, password, seconds):
                self._write(deadline, _USER + "/set", **{".id": _id(row), **desired})
        staged = self._user(deadline, username, owner, password=True)
        if ((row is not None and _id(staged) != _id(row)) or staged["disabled"] != "true"
                or not self._matches(staged, password, seconds)):
            raise RouterError("The staged HotSpot user could not be verified.")
        self._write(deadline, _USER + "/set", **{".id": _id(staged), "disabled": "no"})
        confirmed = self._user(deadline, username, owner, password=True)
        if (_id(confirmed) != _id(staged) or confirmed["disabled"] != "false"
                or not self._matches(confirmed, password, seconds)):
            raise RouterError("The enabled HotSpot user could not be verified.")

    def _matches(self, row, password, seconds):
        return (row.get("profile") == self._profile and row.get("password") == password
                and _seconds(row.get("limit-uptime")) == seconds)

    def revoke_user(self, username, owner):
        """Disable an owned account, remove sessions and cookies, and verify revocation."""
        self._require_hotspot_policy()
        _text(username, "Invalid HotSpot username.")
        _text(owner, "Invalid HotSpot owner marker.")
        deadline = time.monotonic() + _OPERATION_TIMEOUT
        row = self._user(deadline, username, owner)
        row_id = _id(row)
        if row["disabled"] != "true":
            self._write(deadline, _USER + "/set", **{".id": row_id, "disabled": "yes"})
        self._confirm_disabled(deadline, username, owner, row_id)
        for item in self._active(deadline, username):
            self._write(deadline, _ACTIVE + "/remove", **{".id": _id(item)})
        for item in self._cookies(deadline, username):
            self._write(deadline, _COOKIE + "/remove", **{".id": _id(item)})
        if self._active(deadline, username) or self._cookies(deadline, username):
            raise RouterError("HotSpot revocation could not be confirmed.")
        self._confirm_disabled(deadline, username, owner, row_id)

    def _confirm_disabled(self, deadline, username, owner, row_id):
        row = self._user(deadline, username, owner)
        if _id(row) != row_id or row["disabled"] != "true":
            raise RouterError("HotSpot account disable could not be confirmed.")

    def account(self, username, owner):
        """Return complete usage (including one verified live session), or raise."""
        _text(username, "Invalid HotSpot username.")
        _text(owner, "Invalid HotSpot owner marker.")
        deadline = time.monotonic() + _OPERATION_TIMEOUT
        profile = self._verify_policy(deadline)
        before = self._active(deadline, username)
        user = self._user(deadline, username, owner)
        after = self._active(deadline, username)
        limit = _seconds(user.get("limit-uptime"))
        stored = _seconds(user.get("uptime"))
        if user.get("profile") != self._profile or not limit or stored > limit:
            raise RouterError("HotSpot cumulative usage could not be verified.")
        if not before and not after:
            return {"used_seconds": stored, "active": False}
        if (len(before) != 1 or len(after) != 1 or _id(before[0]) != _id(after[0])
                or user["disabled"] != "false"
                or _seconds(profile.get("session-timeout")) != 0
                or any(row.get("radius", "false") not in ("false", "no")
                       for row in (before[0], after[0]))):
            raise RouterError("HotSpot live usage could not be verified.")
        previous = _seconds(before[0].get("uptime"))
        current = _seconds(after[0].get("uptime"))
        left_before = _seconds(before[0].get("session-time-left"))
        left_after = _seconds(after[0].get("session-time-left"))
        used = limit - left_after
        # With no shorter session timeout, the remaining session allowance
        # reflects the remaining cumulative quota; don't add uptime blindly.
        if (current < previous or left_after > left_before or left_before > limit
                or abs((current - previous) - (left_before - left_after)) > 1
                or not max(current, stored) <= used <= stored + current):
            raise RouterError("HotSpot live usage counters did not agree.")
        return {"used_seconds": used, "active": True}


@contextmanager
def connect(policy):
    """Open, authenticate and verify a RouterOS connection.

    TLS uses the system trust store and verifies the configured host. Plaintext
    sends credentials unencrypted by the API and requires allow_plaintext=True;
    select it only on a trusted path (for example, through an encrypted tunnel).
    Omit both profile and rate_limit to allow only router health checks.
    Use one session per thread, reserve managed usernames on the router, and
    serialize writes to the same user across sessions.
    """
    checked = _policy(policy)
    deadline = time.monotonic() + _CONNECT_TIMEOUT
    session = _Session(_open(checked, deadline), checked)
    try:
        rows, done = session._command(deadline, "/login", (
            ("name", checked["api_user"]), ("password", checked["api_password"]),
        ))
        if rows or done:
            raise RouterError("Unsupported RouterOS login response.")
        identity = _one(session._print(deadline, "/system/identity", "name"))
        if identity.get("name") != checked["router_identity"]:
            raise RouterError("RouterOS identity did not match the policy.")
        yield session
    finally:
        session.close()
