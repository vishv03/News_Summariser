"""Clean up article URLs so the same article always gets the same dedupe key."""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from. They never change which article the
# URL points to. Kept short on purpose: removing a parameter a site really needs would merge two
# different articles.
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ocid",
    "cmpid",
    "smid",
    "at_medium",
    "at_campaign",
}
TRACKING_PREFIXES = ("utm_", "ns_")


def _is_tracking(name: str) -> bool:
    name = name.lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def clean_url(url: str) -> str:
    """Return the URL with tracking parameters and fragment removed and the host lowercased."""
    parts = urlsplit(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    # Sorted so the same parameters in a different order give the same key.
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), "")
    )


def url_hash(url: str) -> str:
    """Return the dedupe key for a URL: sha256 of its cleaned form."""
    return hashlib.sha256(clean_url(url).encode("utf-8")).hexdigest()
