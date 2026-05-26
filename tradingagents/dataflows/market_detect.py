"""Market detection for A-share (Chinese) stocks."""

import re

_A_SHARE_RE = re.compile(r'^(\d{6})(\.(SS|SZ|BJ))?$', re.IGNORECASE)


def is_a_share(ticker: str) -> bool:
    """Check if ticker is an A-share stock.

    Matches: 600519, 600519.SS, 000001.SZ, 833171.BJ
    Does not match: AAPL, NVDA, 0700.HK
    """
    return bool(_A_SHARE_RE.match(ticker.strip()))


def extract_a_share_code(ticker: str) -> str:
    """Extract the 6-digit code from an A-share ticker.

    600519.SS -> 600519, 000001 -> 000001
    Raises ValueError if not an A-share ticker.
    """
    m = _A_SHARE_RE.match(ticker.strip())
    if not m:
        raise ValueError(f"Not an A-share ticker: {ticker}")
    return m.group(1)


def get_a_share_exchange(ticker: str) -> str:
    """Return the exchange for an A-share ticker: SH, SZ, or BJ."""
    code = extract_a_share_code(ticker)
    if code.startswith(('60', '68', '90')):
        return 'SH'
    if code.startswith(('00', '30')):
        return 'SZ'
    if code.startswith('8'):
        return 'BJ'
    return 'SZ'


def normalize_a_share_ticker(ticker: str) -> str:
    """Normalize A-share ticker to suffixed form.

    600519 -> 600519.SS, 000001.SZ -> 000001.SZ
    """
    m = _A_SHARE_RE.match(ticker.strip())
    if not m:
        raise ValueError(f"Not an A-share ticker: {ticker}")
    code, suffix = m.group(1), m.group(2)
    if suffix:
        return f"{code}.{suffix.upper()}"
    exchange = get_a_share_exchange(ticker)
    return f"{code}.{'SS' if exchange == 'SH' else exchange}"
