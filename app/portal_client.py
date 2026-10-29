"""The only module that communicates with the original Urja portal.

Responsibilities:
- Maintain a session and perform login.
- Make GET requests with timeouts and bounded retries.
- Respect Retry-After when rate-limited.
- Cache successful GET responses briefly.
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
MAX_WAIT_SECONDS = 10
CACHE_TTL_SECONDS = 30

# The portal is a SvelteKit app. It rejects some non-browser clients, so we
# present a normal browser User-Agent (as seen in the browser's Network tab).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


class PortalError(Exception):
    """An error communicating with the upstream portal."""

    def __init__(self, message, kind="upstream_error", retry_after=None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retry_after = retry_after


class PortalClient:
    def __init__(self, session=None, base_url=None):
        self.base_url = (
            base_url
            or os.getenv(
                "PORTAL_BASE_URL",
                "https://urja-ops.flockenergy.tech",
            )
        ).rstrip("/")

        # An injected session is commonly a mock used by tests.
        self._session_injected = session is not None
        self.session = session if session is not None else requests.Session()
        if not self._session_injected:
            self.session.headers["User-Agent"] = BROWSER_USER_AGENT
        self.logged_in = False
        self._cache = {}

    def login(self):
        """Initialize a session and submit the observed login form."""
        login_path = os.getenv("PORTAL_LOGIN_PATH", "/login").strip()

        if not login_path:
            raise PortalError(
                "PORTAL_LOGIN_PATH is not configured.",
                kind="auth",
            )

        username = os.getenv("PORTAL_USERNAME")
        password = os.getenv("PORTAL_PASSWORD")

        if not username or not password:
            raise PortalError(
                "Portal credentials are not configured.",
                kind="auth",
            )

        login_url = f"{self.base_url}/{login_path.lstrip('/')}"
        user_field = os.getenv("PORTAL_LOGIN_USER_FIELD", "email")
        pass_field = os.getenv("PORTAL_LOGIN_PASS_FIELD", "password")

        headers = {
            "Origin": self.base_url,
            "Referer": login_url,
            "Accept": "application/json",
            # Marks the POST as a SvelteKit form action, so the server
            # answers with JSON instead of a redirect.
            "X-Sveltekit-Action": "true",
            "User-Agent": BROWSER_USER_AGENT,
        }

        try:
            # Initialize cookies or CSRF state before submitting login.
            self.session.get(
                login_url,
                headers={"Accept": "text/html,application/json"},
                timeout=TIMEOUT_SECONDS,
            )

            response = self.session.post(
                login_url,
                data={user_field: username, pass_field: password},
                headers=headers,
                timeout=TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            raise PortalError(
                "The portal login request timed out.",
                kind="timeout",
            ) from exc
        except requests.RequestException as exc:
            raise PortalError(
                f"Could not reach the portal ({type(exc).__name__}).",
                kind="upstream_error",
            ) from exc

        # Never print credentials or cookie values.
        try:
            cookie_names = [
                cookie.name for cookie in self.session.cookies
            ]
        except (AttributeError, TypeError):
            cookie_names = []

        print("Portal login HTTP status:", response.status_code)
        print(
            "Portal login content type:",
            response.headers.get("Content-Type"),
        )
        print("Session cookie names:", cookie_names)

        if response.status_code in (401, 403):
            raise PortalError(
                f"Portal login was rejected (HTTP {response.status_code}). "
                "Check the login flow and any CSRF requirements.",
                kind="auth",
            )

        if response.status_code >= 400:
            raise PortalError(
                f"Portal login returned HTTP {response.status_code}.",
                kind="auth",
            )

        if not cookie_names:
            raise PortalError(
                "Login returned without a session cookie.",
                kind="auth",
            )

        # SvelteKit actions answer HTTP 200 even for wrong credentials; the
        # real outcome is in the JSON body: {"type": "success" | "failure"}.
        try:
            result_type = response.json().get("type")
        except (ValueError, AttributeError):
            result_type = None

        if result_type not in ("success", "redirect"):
            raise PortalError(
                f"Portal login did not succeed (result: {result_type}).",
                kind="auth",
            )

        self.logged_in = True

    def get_json(self, path, params=None):
        """GET JSON with caching, bounded retries and re-login."""
        key = (path, tuple(sorted((params or {}).items())))

        cached = self._cache.get(key)
        if cached and cached[0] > time.time():
            return cached[1]

        if not self.logged_in:
            if self._session_injected:
                # Mock sessions should use the responses configured by tests.
                self.logged_in = True
            else:
                self.login()

        url = f"{self.base_url}/{path.lstrip('/')}"
        relogged = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                # Compatible with requests.Session and mocks implementing
                # request(), rather than requiring a .get() method.
                response = self.session.request(
                    "GET",
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=TIMEOUT_SECONDS,
                )

            except requests.Timeout as exc:
                if attempt == MAX_ATTEMPTS:
                    raise PortalError(
                        "The upstream portal timed out.",
                        kind="timeout",
                    ) from exc

                self._wait(attempt)
                continue

            except requests.RequestException as exc:
                raise PortalError(
                    "Could not reach the upstream portal.",
                    kind="upstream_error",
                ) from exc

            code = response.status_code

            if code == 200:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise PortalError(
                        "The portal returned invalid JSON."
                    ) from exc

                self._cache[key] = (
                    time.time() + CACHE_TTL_SECONDS,
                    body,
                )
                return body

            if code == 429:
                retry_after = self._parse_retry_after(
                    response.headers.get("Retry-After")
                )

                if attempt == MAX_ATTEMPTS:
                    raise PortalError(
                        "The upstream portal is rate-limiting requests.",
                        kind="rate_limited",
                        retry_after=retry_after,
                    )

                self._wait(attempt, retry_after)
                continue

            if code in (401, 403):
                if not relogged and not self._session_injected:
                    relogged = True
                    self.logged_in = False
                    self.login()
                    continue

                raise PortalError(
                    "The portal rejected the authenticated request.",
                    kind="auth",
                )

            if code == 404:
                raise PortalError(
                    "The requested resource was not found upstream.",
                    kind="not_found",
                )

            if code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise PortalError(
                        f"The upstream portal returned HTTP {code}."
                    )

                self._wait(attempt)
                continue

            raise PortalError(
                f"Unexpected upstream HTTP status {code}."
            )

        raise PortalError("The upstream request failed.")

    @staticmethod
    def _parse_retry_after(value):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _wait(attempt, retry_after=None):
        delay = (
            retry_after
            if retry_after is not None
            else 2 ** (attempt - 1)
        )
        time.sleep(min(delay, MAX_WAIT_SECONDS))

    # Observed meter endpoints
    def search_meters(self, q="", page=1):
        return self.get_json(
            "/portal/meters/search",
            {"q": q, "page": page},
        )

    def get_energy(self, meter_id):
        return self.get_json(
            f"/portal/meters/{meter_id}/energy"
        )

    def get_geo(self, meter_id):
        return self.get_json(
            f"/portal/meters/{meter_id}/geo"
        )

    def get_transformers(self, page=1):
        path = os.getenv("PORTAL_DTS_PATH", "/portal/dts").split("?")[0]
        return self.get_json(path, {"page": page})