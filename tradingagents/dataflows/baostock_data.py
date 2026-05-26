"""BaoStock data provider for A-share OHLCV, technical indicators, and index data.

Replaces akshare for stock price data (push2his.eastmoney.com is blocked).
BaoStock uses its own data server, independent of eastmoney.
"""

import logging
import os
import time
from datetime import datetime

import baostock as bs
import pandas as pd
from stockstats import wrap

from .config import get_config
from .market_detect import extract_a_share_code
from .stockstats_utils import _clean_dataframe

logger = logging.getLogger(__name__)

# Login once at module load
_logged_in = False


def _ensure_login():
    global _logged_in
    if not _logged_in:
        bs.login()
        _logged_in = True


def _to_bs_code(ticker: str) -> str:
    """Convert ticker to baostock format: sh.600519 / sz.000725."""
    code = extract_a_share_code(ticker)
    if code.startswith("6"):
        return f"sh.{code}"
    elif code.startswith(("0", "3")):
        return f"sz.{code}"
    elif code.startswith(("8", "4")):
        return f"bj.{code}"
    return f"sh.{code}"


def _to_bs_index_code(symbol: str) -> str:
    """Convert index code to baostock format.

    Index codes: sh.000300 (CSI 300), sh.000001 (SSE Composite), sz.399001 (SZSE Component).
    """
    code = symbol.replace(".", "").replace("SH", "").replace("SZ", "").strip()
    if code.startswith(("0", "39")):
        # 000xxx indices on Shanghai, 399xxx on Shenzhen
        if code.startswith("39"):
            return f"sz.{code}"
        return f"sh.{code}"
    return f"sh.{code}"


# Column mapping: baostock field names -> standard English names
_BS_COL_MAP = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "amount": "Amount",
    "pctChg": "Change%",
    "turn": "Turnover",
}


def _query_kline(bs_code: str, start_date: str, end_date: str,
                 frequency: str = "d", adjustflag: str = "2") -> pd.DataFrame:
    """Query baostock kline data and return a clean DataFrame.

    adjustflag: "1"=后复权, "2"=前复权, "3"=不复权
    """
    _ensure_login()
    fields = "date,open,high,low,close,volume,amount,pctChg,turn"
    rs = bs.query_history_k_data_plus(
        bs_code, fields,
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        raise ValueError(f"baostock error: {rs.error_msg}")

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=rs.fields)
    # Convert numeric columns
    for col in ["open", "high", "low", "close", "volume", "amount", "pctChg", "turn"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df.rename(columns=_BS_COL_MAP, inplace=True)
    return df


# ---------------------------------------------------------------------------
# OHLCV
# ---------------------------------------------------------------------------

def get_AShare_data_online(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch A-share OHLCV data via baostock. Returns CSV string with header."""
    try:
        bs_code = _to_bs_code(symbol)
        df = _query_kline(bs_code, start_date, end_date)

        if df.empty:
            return f"No data found for A-share '{symbol}' between {start_date} and {end_date}"

        keep = [c for c in ["Date", "Open", "High", "Low", "Close",
                            "Volume", "Amount", "Change%", "Turnover"] if c in df.columns]
        df = df[keep]
        numeric_cols = [c for c in ["Open", "High", "Low", "Close"] if c in df.columns]
        df[numeric_cols] = df[numeric_cols].round(2)

        csv_string = df.to_csv(index=False)
        header = f"# A-share data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(df)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving A-share data for {symbol}: {str(e)}"


# ---------------------------------------------------------------------------
# Technical Indicators (via stockstats on baostock OHLCV)
# ---------------------------------------------------------------------------

def load_ohlcv_baostock(symbol: str, curr_date: str) -> pd.DataFrame:
    """Load A-share OHLCV for stockstats, with caching and look-ahead prevention."""
    from .stockstats_utils import safe_ticker_component

    safe_symbol = safe_ticker_component(symbol)
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today = pd.Timestamp.today()
    start = (today - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    cache_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-BaoStock-data-{start}-{end}.csv",
    )

    if os.path.exists(cache_file):
        data = pd.read_csv(cache_file, on_bad_lines="skip", encoding="utf-8")
    else:
        bs_code = _to_bs_code(symbol)
        df = _query_kline(bs_code, start, end)

        if df.empty:
            raise ValueError(f"baostock returned empty data for {symbol}")

        keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
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
    """Calculate technical indicator from A-share OHLCV data using stockstats."""
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
        data = load_ohlcv_baostock(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        df[indicator]  # trigger stockstats calculation

        indicator_data = {}
        for _, row in df.iterrows():
            date_str = row["Date"]
            val = row[indicator]
            indicator_data[date_str] = "N/A" if pd.isna(val) else str(val)

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
# Index Data
# ---------------------------------------------------------------------------

def get_AShare_index_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch A-share index OHLCV data via baostock."""
    try:
        bs_code = _to_bs_index_code(symbol)
        df = _query_kline(bs_code, start_date, end_date)

        if df.empty:
            return f"No index data found for '{symbol}' between {start_date} and {end_date}"

        keep = [c for c in ["Date", "Open", "High", "Low", "Close",
                            "Volume", "Amount"] if c in df.columns]
        df = df[keep]

        csv_string = df.to_csv(index=False)
        header = f"# A-share index data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(df)}\n\n"
        return header + csv_string

    except Exception as e:
        return f"Error retrieving index data for {symbol}: {str(e)}"
