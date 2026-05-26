"""A-share data fetching via akshare.

All function signatures match the vendor interface expected by
interface.py:route_to_vendor().  Each function extracts the 6-digit
A-share code from the ticker, calls the appropriate akshare API, and
returns data in the same format as the yfinance equivalents.
"""

import logging
import os
import socket
import time
from datetime import datetime
from typing import Optional

import pandas as pd
from stockstats import wrap

from .config import get_config
from .market_detect import extract_a_share_code, is_a_share
from .stockstats_utils import _clean_dataframe
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anti-scraping: monkey-patch requests.get for eastmoney.com
# Ported from TradingAgents-CN's akshare provider.
# EastMoney detects TLS fingerprints and drops non-browser connections.
# curl_cffi with impersonate="chrome120" bypasses this.
# ---------------------------------------------------------------------------

def _init_akshare_anti_scraping():
    """Patch requests.get to bypass EastMoney anti-scraping.

    - Uses curl_cffi with Chrome 120 TLS fingerprint for eastmoney.com
    - Injects browser headers for all requests
    - Enforces 0.5s rate limit on eastmoney.com requests
    - Retries SSL errors up to 3 times
    """
    import requests

    if hasattr(requests, '_akshare_headers_patched'):
        return

    try:
        from curl_cffi import requests as curl_requests
        use_curl_cffi = True
    except ImportError:
        use_curl_cffi = False
        logger.warning("curl_cffi not installed, using standard requests (may be blocked by anti-scraping)")

    original_get = requests.get
    last_request_time = {'time': 0}

    def patched_get(url, **kwargs):
        # Rate limit eastmoney.com requests (0.5s minimum interval)
        if 'eastmoney.com' in url:
            elapsed = time.time() - last_request_time['time']
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            last_request_time['time'] = time.time()

        # curl_cffi path: bypass TLS fingerprinting for eastmoney.com
        if use_curl_cffi and 'eastmoney.com' in url:
            try:
                curl_kwargs = {
                    'timeout': kwargs.get('timeout', 10),
                    'impersonate': "chrome120",
                }
                if 'params' in kwargs:
                    curl_kwargs['params'] = kwargs['params']
                if 'data' in kwargs:
                    curl_kwargs['data'] = kwargs['data']
                if 'json' in kwargs:
                    curl_kwargs['json'] = kwargs['json']
                return curl_requests.get(url, **curl_kwargs)
            except Exception as e:
                error_msg = str(e)
                if 'invalid library' not in error_msg and '400' not in error_msg:
                    logger.warning(f"curl_cffi failed, falling back to requests: {e}")

        # Bypass proxy for eastmoney.com (VPNs often break these connections)
        if 'eastmoney.com' in url:
            kwargs['proxies'] = {'http': None, 'https': None}

        # Standard requests fallback: inject browser headers
        if 'headers' not in kwargs or kwargs['headers'] is None:
            kwargs['headers'] = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.eastmoney.com/',
                'Connection': 'keep-alive',
            }

        # Retry on SSL errors (up to 3 attempts)
        for attempt in range(3):
            try:
                return original_get(url, **kwargs)
            except Exception as e:
                error_str = str(e)
                is_ssl = any(k in error_str for k in ('SSL', 'ssl', 'UNEXPECTED_EOF_WHILE_READING'))
                if is_ssl and attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

    requests.get = patched_get
    requests._akshare_headers_patched = True
    socket.setdefaulttimeout(60)
    logger.info("akshare anti-scraping patch applied" + (" (curl_cffi enabled)" if use_curl_cffi else ""))


# Apply patch at module import time
_init_akshare_anti_scraping()


# akshare column name mapping (Chinese -> English)
_COL_MAP = {
    '日期': 'Date',
    '股票代码': 'Symbol',
    '开盘': 'Open',
    '收盘': 'Close',
    '最高': 'High',
    '最低': 'Low',
    '成交量': 'Volume',
    '成交额': 'Amount',
    '振幅': 'Amplitude',
    '涨跌幅': 'Change%',
    '涨跌额': 'Change',
    '换手率': 'Turnover',
}

def _fmt_date(d: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD for akshare."""
    return d.replace("-", "")


def _extract_code(ticker: str) -> str:
    """Extract 6-digit code, handling both bare and suffixed forms."""
    return extract_a_share_code(ticker)


def _extract_code_with_market(ticker: str) -> str:
    """Extract 6-digit code and prepend SH/SZ/BJ market prefix.

    akshare's financial statement functions (stock_balance_sheet_by_report_em etc.)
    require a symbol like 'SZ000725' or 'SH600519', not bare '000725'.
    """
    code = extract_a_share_code(ticker)
    if code.startswith("6"):
        return f"SH{code}"
    elif code.startswith(("0", "3")):
        return f"SZ{code}"
    elif code.startswith(("8", "4")):
        return f"BJ{code}"
    return f"SH{code}"


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def get_AShare_data_online(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch A-share OHLCV data. Returns CSV string with header."""
    try:
        import akshare as ak

        code = _extract_code(symbol)
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=_fmt_date(start_date),
            end_date=_fmt_date(end_date),
            adjust="qfq",
        )

        if df is None or df.empty:
            return f"No data found for A-share '{symbol}' between {start_date} and {end_date}"

        df = df.rename(columns=_COL_MAP)

        # Keep standard OHLCV columns + extras
        keep = [c for c in ['Date', 'Open', 'High', 'Low', 'Close', 'Volume',
                            'Amount', 'Change%', 'Turnover'] if c in df.columns]
        df = df[keep]

        numeric_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
        df[numeric_cols] = df[numeric_cols].round(2)

        csv_string = df.to_csv(index=False)
        header = f"# A-share data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(df)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving A-share data for {symbol}: {str(e)}"


# ---------------------------------------------------------------------------
# Technical Indicators (via stockstats on akshare OHLCV)
# ---------------------------------------------------------------------------

def load_ohlcv_akshare(symbol: str, curr_date: str) -> pd.DataFrame:
    """Load A-share OHLCV for stockstats, with caching and look-ahead prevention.

    Parallel to stockstats_utils.load_ohlcv() but uses akshare instead of yfinance.
    """
    safe_symbol = safe_ticker_component(symbol)
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today = pd.Timestamp.today()
    start = (today - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    cache_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-AKShare-data-{start}-{end}.csv",
    )

    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, on_bad_lines="skip", encoding="utf-8")
    else:
        import akshare as ak

        code = _extract_code(symbol)

        # Retry logic for large data requests (5-year download can be dropped)
        df = None
        for attempt in range(3):
            try:
        
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=_fmt_date(start),
                    end_date=_fmt_date(end),
                    adjust="qfq",
                )
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"akshare download attempt {attempt+1} failed for {symbol}: {e}, retrying...")
                    time.sleep(2 * (attempt + 1))
                else:
                    raise

        if df is None or df.empty:
            raise ValueError(f"akshare returned empty data for {symbol}")

        df = df.rename(columns=_COL_MAP)
        keep = [c for c in ['Date', 'Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
        df = df[keep]
        df.to_csv(cache_file, index=False, encoding="utf-8")
        data = df

    data = _clean_dataframe(data)
    data = data[data["Date"] <= curr_date_dt]
    return data


def get_AShare_stock_stats_indicators_window(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
) -> str:
    """Calculate technical indicator from A-share OHLCV data using stockstats.

    Same interface as y_finance.get_stock_stats_indicators_window().
    Returns a formatted string, not a dict.
    """
    from dateutil.relativedelta import relativedelta

    best_ind_params = {
        "close_50_sma": "50 SMA: A medium-term trend indicator.",
        "close_200_sma": "200 SMA: A long-term trend benchmark.",
        "close_10_ema": "10 EMA: A responsive short-term average.",
        "macd": "MACD: Computes momentum via differences of EMAs.",
        "macds": "MACD Signal: An EMA smoothing of the MACD line.",
        "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal.",
        "rsi": "RSI: Measures momentum to flag overbought/oversold conditions.",
        "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands.",
        "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line.",
        "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line.",
        "atr": "ATR: Averages true range to measure volatility.",
        "vwma": "VWMA: A moving average weighted by volume.",
        "mfi": "MFI: Money Flow Index combining price and volume.",
    }

    if indicator not in best_ind_params:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(best_ind_params.keys())}"
        )

    try:
        data = load_ohlcv_akshare(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats calculation

        indicator_data = {}
        for _, row in df.iterrows():
            date_str = row["Date"]
            val = row[indicator]
            indicator_data[date_str] = "N/A" if pd.isna(val) else str(val)

        # Build date range
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before = curr_date_dt - relativedelta(days=look_back_days)

        ind_string = ""
        current_dt = curr_date_dt
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            value = indicator_data.get(date_str, "N/A: Not a trading day (weekend or holiday)")
            ind_string += f"{date_str}: {value}\n"
            current_dt -= relativedelta(days=1)

        result_str = (
            f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
            + ind_string
            + "\n\n"
            + best_ind_params.get(indicator, "No description available.")
        )
        return result_str

    except Exception as e:
        return f"Error calculating indicator '{indicator}' for {symbol}: {str(e)}"


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

def get_AShare_fundamentals(
    ticker: str,
    curr_date: str = None,
) -> str:
    """Get A-share company fundamentals overview."""
    try:
        import akshare as ak

        code = _extract_code(ticker)
        lines = [f"# Company Fundamentals for {ticker}", ""]

        # Basic info (name, industry, area, list date)

        try:
            info_df = ak.stock_individual_info_em(symbol=code)
            if info_df is not None and not info_df.empty:
                for _, row in info_df.iterrows():
                    item = str(row.iloc[0]) if len(row) > 0 else ""
                    value = str(row.iloc[1]) if len(row) > 1 else ""
                    if item and value:
                        lines.append(f"{item}: {value}")
                lines.append("")
        except Exception:
            pass

        # PE/PB/market cap already available from stock_individual_info_em above

        header = f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


# ---------------------------------------------------------------------------
# Financial Statements
# ---------------------------------------------------------------------------

def get_AShare_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get A-share balance sheet data (last 20 periods to keep context manageable)."""
    try:
        import akshare as ak

        code = _extract_code_with_market(ticker)

        df = ak.stock_balance_sheet_by_report_em(symbol=code)

        if df is None or df.empty:
            return f"No balance sheet data found for A-share '{ticker}'"

        # Limit to last 20 periods (~5 years of quarterly data) to avoid
        # blowing up the LLM context window (full history is 100+ rows × 200+ cols)
        df = df.head(20)

        csv_string = df.to_csv(index=False)
        header = f"# Balance Sheet data for {ticker} ({freq}, last 20 periods)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_AShare_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get A-share cash flow statement (last 20 periods to keep context manageable)."""
    try:
        import akshare as ak

        code = _extract_code_with_market(ticker)

        df = ak.stock_cash_flow_sheet_by_report_em(symbol=code)

        if df is None or df.empty:
            return f"No cash flow data found for A-share '{ticker}'"

        df = df.head(20)

        csv_string = df.to_csv(index=False)
        header = f"# Cash Flow data for {ticker} ({freq}, last 20 periods)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_AShare_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str = None,
) -> str:
    """Get A-share income statement (last 20 periods to keep context manageable)."""
    try:
        import akshare as ak

        code = _extract_code_with_market(ticker)

        df = ak.stock_profit_sheet_by_report_em(symbol=code)

        if df is None or df.empty:
            return f"No income statement data found for A-share '{ticker}'"

        df = df.head(20)

        csv_string = df.to_csv(index=False)
        header = f"# Income Statement data for {ticker} ({freq}, last 20 periods)\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def get_AShare_news(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Get A-share stock news from EastMoney."""
    try:
        import akshare as ak

        code = _extract_code(ticker)
        config = get_config()
        limit = config.get("news_article_limit", 20)


        df = ak.stock_news_em(symbol=code)

        if df is None or df.empty:
            return f"No news found for A-share '{ticker}'"

        # Filter by date range if possible
        if '发布时间' in df.columns:
            df['发布时间'] = pd.to_datetime(df['发布时间'], errors='coerce')
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            df = df[(df['发布时间'] >= start_dt) & (df['发布时间'] <= end_dt)]

        df = df.head(limit)

        articles = []
        for _, row in df.iterrows():
            title = str(row.get('新闻标题', row.get('标题', '')))
            content = str(row.get('新闻内容', row.get('内容', '')))
            source = str(row.get('文章来源', row.get('来源', '')))
            pub_time = str(row.get('发布时间', ''))
            url = str(row.get('新闻链接', row.get('链接', '')))

            article = f"### {title}\n"
            if source:
                article += f"**Source:** {source}\n"
            if pub_time:
                article += f"**Published:** {pub_time}\n"
            if content and content != 'nan':
                # Truncate long content
                if len(content) > 500:
                    content = content[:500] + "..."
                article += f"\n{content}\n"
            if url and url != 'nan':
                article += f"\n[Link]({url})\n"
            articles.append(article)

        header = f"# A-share News for {ticker} ({start_date} to {end_date})\n"
        header += f"# Total articles: {len(articles)}\n\n"
        return header + "\n---\n".join(articles)

    except Exception as e:
        return f"Error retrieving news for {ticker}: {str(e)}"


def get_AShare_global_news(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Get Chinese macro news from CCTV."""
    try:
        import akshare as ak

        config = get_config()
        if limit is None:
            limit = config.get("global_news_article_limit", 10)
        if look_back_days is None:
            look_back_days = config.get("global_news_lookback_days", 7)

        articles = []
        # Fetch CCTV news for multiple days
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        for day_offset in range(look_back_days):
            fetch_date = curr_dt - __import__('datetime').timedelta(days=day_offset)
            date_str = fetch_date.strftime("%Y%m%d")
            try:
                df = ak.news_cctv(date=date_str)
                if df is not None and not df.empty:
                    for _, row in df.head(limit).iterrows():
                        title = str(row.get('title', ''))
                        content = str(row.get('content', ''))
                        date_val = str(row.get('date', ''))
                        article = f"### {title}\n"
                        if date_val:
                            article += f"**Date:** {date_val}\n"
                        if content and content != 'nan':
                            if len(content) > 500:
                                content = content[:500] + "..."
                            article += f"\n{content}\n"
                        articles.append(article)
            except Exception as e:
                logger.warning(f"CCTV news fetch failed for {date_str}: {e}")
            if len(articles) >= limit:
                break

        if not articles:
            return f"No global news available for {curr_date}"

        header = f"# CCTV Financial News for {curr_date}\n"
        header += f"# Total articles: {len(articles)}\n\n"
        return header + "\n---\n".join(articles)

    except Exception as e:
        return f"Error retrieving global news: {str(e)}"


# ---------------------------------------------------------------------------
# Insider Transactions (not available for A-shares)
# ---------------------------------------------------------------------------

def get_AShare_insider_transactions(ticker: str) -> str:
    """A-shares do not have SEC-style insider transaction disclosures."""
    return (
        f"Insider transaction data is not available for A-share stock '{ticker}'. "
        "China does not have a public insider trading disclosure system "
        "equivalent to the US SEC."
    )


# ---------------------------------------------------------------------------
# Index Data (for benchmark / alpha calculation)
# ---------------------------------------------------------------------------

def get_AShare_index_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch A-share index OHLCV data (e.g., CSI 300, Shanghai Composite).

    symbol should be the raw index code, e.g., '000300' for CSI 300.
    """
    try:
        import akshare as ak


        df = ak.index_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=_fmt_date(start_date),
            end_date=_fmt_date(end_date),
        )

        if df is None or df.empty:
            return f"No index data found for '{symbol}' between {start_date} and {end_date}"

        df = df.rename(columns=_COL_MAP)
        csv_string = df.to_csv(index=False)
        header = f"# A-share index data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(df)}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving index data for {symbol}: {str(e)}"
