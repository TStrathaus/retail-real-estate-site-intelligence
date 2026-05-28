"""Strava Global Heatmap authentication helper — host- and path-agnostic.

Strava has two authenticated-tile URL families in production:

  • Old format:  `heatmap-external-{a,b,c}.strava.com/tiles-auth/...`
                  with auth tokens embedded in the URL query string
                  (`?Key-Pair-Id=…&Policy=…&Signature=…`)
  • New format:  `content-{a,b,c}.strava.com/identified/globalheat/...`
                  with **no auth in the URL** — CloudFront authenticates
                  via cookies on the browser. Headers say:
                  *"Missing Key-Pair-Id query parameter or cookie value"*

Empirically (May 2026) CloudFront accepts auth EITHER as query params
OR as cookies — confirmed by appending placeholder query auth to a
new-format URL and seeing `InvalidKey` instead of `MissingKey`. So the
solution is: take the user's pasted URL, append their 3 CloudFront
cookies as query parameters. Folium fetches normally; the browser
doesn't need to be on strava.com.

Workflow for the user:
  1. Log into strava.com/maps/global-heatmap.
  2. DevTools → Network → right-click any tile → Copy URL → paste here.
  3. DevTools → Application → Cookies → .strava.com → copy values for
     `CloudFront-Key-Pair-Id`, `CloudFront-Policy`, `CloudFront-Signature`.
  4. We combine URL + cookies into a portable, signed tile URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


# Path-tail pattern: /{z}/{x}/{y}[.ext], anchored to end-of-path
_TILE_XYZ_RE = re.compile(
    r"/(\d+)/(\d+)/(\d+)(?P<ext>\.[a-zA-Z0-9]+)?(?P<tail>[?#].*|$)"
)

_KNOWN_ACTIVITIES = {"all", "run", "ride", "water", "winter"}
_KNOWN_COLORS = {"hot", "blue", "purple", "gray", "grayscale", "bluered",
                  "red", "mobileblue"}


@dataclass
class StravaAuth:
    """Templated tile URL + optional cookies for query-string auth."""
    tile_url_template: str         # has {z}/{x}/{y} placeholders; may
                                   # or may not include auth params
    host: str
    activity_hint: str = ""
    color_hint: str = ""
    # Cookies appended at request time (new-format URLs need this)
    key_pair_id: str = ""
    policy: str = ""
    signature: str = ""

    @property
    def has_url_auth(self) -> bool:
        """True if the URL itself already contains CloudFront auth params."""
        return "Key-Pair-Id=" in self.tile_url_template

    @property
    def has_cookie_auth(self) -> bool:
        return bool(self.key_pair_id and self.policy and self.signature)

    @property
    def is_authenticated(self) -> bool:
        return self.has_url_auth or self.has_cookie_auth

    def tile_url(self) -> str:
        """Return the final URL template, appending cookies as query
        parameters if they're set and the URL doesn't already have auth."""
        url = self.tile_url_template
        if self.has_cookie_auth and not self.has_url_auth:
            sep = "&" if "?" in url else "?"
            url = (
                f"{url}{sep}"
                f"Key-Pair-Id={self.key_pair_id}"
                f"&Policy={self.policy}"
                f"&Signature={self.signature}"
            )
        return url

    def fingerprint(self) -> str:
        """Short UI display — never reveals the secret query string."""
        bits = [self.host]
        if self.activity_hint:
            bits.append(self.activity_hint)
        if self.color_hint:
            bits.append(self.color_hint)
        if self.is_authenticated:
            src = "url-auth" if self.has_url_auth else "cookie-auth"
            kp = self.key_pair_id[-6:] if self.has_cookie_auth else ""
            bits.append(f"{src}{(' …' + kp) if kp else ''}")
        return " · ".join(bits)


def parse_sample_url(url: str) -> StravaAuth | None:
    """Convert any Strava heatmap tile URL into a folium-ready template.

    Accepts both old and new Strava URL families. Returns None for
    non-Strava URLs or URLs without a `/z/x/y[.ext]` tail. The returned
    StravaAuth may still need cookies if the URL doesn't already
    contain Key-Pair-Id (i.e. new-format URLs).
    """
    if not url:
        return None
    url = url.strip()
    if "strava.com" not in url.lower():
        return None

    parsed = urlparse(url)
    match = _TILE_XYZ_RE.search(parsed.path)
    if not match:
        return None

    z, x, y = match.group(1), match.group(2), match.group(3)
    ext = match.group("ext") or ""
    needle = f"/{z}/{x}/{y}{ext}"
    idx = url.rfind(needle)
    if idx < 0:
        return None
    templated = (
        url[:idx] + f"/{{z}}/{{x}}/{{y}}{ext}" + url[idx + len(needle):]
    )

    activity_hint = ""
    color_hint = ""
    for p in (s for s in parsed.path.split("/") if s):
        lp = p.lower()
        if lp in _KNOWN_ACTIVITIES and not activity_hint:
            activity_hint = lp
        elif lp in _KNOWN_COLORS and not color_hint:
            color_hint = lp

    return StravaAuth(
        tile_url_template=templated,
        host=parsed.hostname or "strava.com",
        activity_hint=activity_hint,
        color_hint=color_hint,
    )


def attach_cookies(auth: StravaAuth, key_pair_id: str,
                    policy: str, signature: str) -> StravaAuth:
    """Return a new StravaAuth with the cookies attached."""
    return StravaAuth(
        tile_url_template=auth.tile_url_template,
        host=auth.host,
        activity_hint=auth.activity_hint,
        color_hint=auth.color_hint,
        key_pair_id=key_pair_id.strip(),
        policy=policy.strip(),
        signature=signature.strip(),
    )
