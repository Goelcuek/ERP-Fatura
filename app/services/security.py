"""Two network-facing defences for the shop LAN.

LoginThrottle  slows password guessing: after a handful of wrong passwords for the same account
               or from the same device, further attempts are refused for a growing cool-off. In
               memory, per process — a restart clears it, which is fine for the threat (a guessing
               bot on the network, not a global service).

host_allowed   rejects requests whose Host header is not one of this PC's own names/addresses. This
               closes "DNS rebinding": a web page the technician visits cannot make their browser
               reach the app under an attacker-controlled hostname, because that hostname is refused.
"""

import ipaddress
import threading
import time

# login throttle
THRESHOLD = 5          # wrong tries before the first lock
WINDOW = 900           # a quiet spell this long (s) forgives earlier misses
BASE_LOCK = 30         # first lock length (s); doubles each further miss…
MAX_LOCK = 900         # …up to this


class LoginThrottle:
    def __init__(self, threshold=THRESHOLD, window=WINDOW, base_lock=BASE_LOCK, max_lock=MAX_LOCK):
        self.threshold, self.window, self.base_lock, self.max_lock = threshold, window, base_lock, max_lock
        self._state = {}  # key -> [fails, last_ts, locked_until]
        self._lock = threading.Lock()

    def _now(self):
        return time.monotonic()

    def retry_after(self, *keys):
        """Seconds the caller must wait, or 0 if a login attempt is allowed now."""
        now = self._now()
        wait = 0
        with self._lock:
            self._prune(now)
            for key in keys:
                st = self._state.get(key)
                if st and now < st[2]:
                    wait = max(wait, st[2] - now)
        return int(wait) + 1 if wait else 0

    def record_failure(self, *keys):
        now = self._now()
        with self._lock:
            for key in keys:
                st = self._state.get(key)
                if not st or now - st[1] > self.window:  # first miss, or a clean window since the last
                    st = [0, now, 0.0]
                st[0] += 1
                st[1] = now
                if st[0] >= self.threshold:
                    lock = min(self.base_lock * (2 ** (st[0] - self.threshold)), self.max_lock)
                    st[2] = now + lock
                self._state[key] = st

    def record_success(self, *keys):
        with self._lock:
            for key in keys:
                self._state.pop(key, None)

    def _prune(self, now):
        dead = [k for k, st in self._state.items() if now - st[1] > self.window and now >= st[2]]
        for k in dead:
            del self._state[k]


# host allow-list
_hosts_cache = (0.0, frozenset())


def _allowed_names(data_dir=None):
    from ..tls import local_addresses

    names, ips = local_addresses()  # private IPs + localhost/.local/hostname of this PC
    allowed = {n.lower() for n in names} | set(ips) | {"localhost", "127.0.0.1"}
    return frozenset(allowed)


def allowed_hosts(ttl=60):
    """This PC's own names and addresses, refreshed at most once per `ttl` seconds."""
    global _hosts_cache
    now = time.monotonic()
    if now - _hosts_cache[0] > ttl:
        _hosts_cache = (now, _allowed_names())
    return _hosts_cache[1]


def _is_private_ip(host):
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host):
    """True if `host` (a Host header, maybe with a port) is one of this PC's own names/addresses.

    A bare private-IP literal is always accepted: only a browser already pointed at that LAN
    address can send it, so it can't come from an attacker's public domain. Any other name must
    match this PC exactly, which is what blocks DNS-rebinding hostnames.
    """
    if not host:
        return False
    name = host.rsplit(":", 1)[0].strip("[]").lower() if host.count(":") <= 1 else host.strip("[]").lower()
    return name in allowed_hosts() or _is_private_ip(name)
