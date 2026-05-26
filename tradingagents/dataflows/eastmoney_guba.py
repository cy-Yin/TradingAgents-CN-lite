"""EastMoney Guba (stock forum) scraper for A-share retail sentiment.

Fetches posts from the EastMoney stock forum (guba.eastmoney.com) and
provides basic keyword-based sentiment analysis.  This is the A-share
equivalent of the Reddit/StockTwits data used for US stocks.
"""

import json
import logging
import re
import time
from typing import Dict

import pandas as pd
import requests

from .market_detect import extract_a_share_code
# Ensure anti-scraping patch is applied before any eastmoney.com requests
from . import akshare_data  # noqa: F401

logger = logging.getLogger(__name__)

# Positive / negative keyword lists for Chinese stock sentiment
_POSITIVE_WORDS = [
    '上涨', '增长', '利好', '看好', '买入', '推荐', '强势', '突破',
    '创新高', '涨停', '大涨', '爆发', '牛', '翻倍', '加仓', '满仓',
    '起飞', '拉升', '反弹', '回暖', '放量', '主力', '吸筹', '底部',
]

_NEGATIVE_WORDS = [
    '下跌', '下降', '利空', '看空', '卖出', '风险', '跌破', '创新低',
    '亏损', '跌停', '暴跌', '崩盘', '熊', '割肉', '减仓', '清仓',
    '套牢', '跳水', '阴跌', '缩量', '出货', '洗盘', '顶部', '见顶',
]

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://guba.eastmoney.com/',
}


def fetch_guba_posts(ticker: str, limit: int = 30) -> str:
    """Fetch posts from EastMoney Guba for an A-share stock.

    Scrapes the guba HTML page and extracts embedded ``var article_list``
    JSON data (the old JSON API endpoint is dead).  Returns a formatted
    string with post titles, read counts, and comment counts.
    Falls back to ``_fallback_guba`` if scraping fails.
    """
    try:
        code = extract_a_share_code(ticker)
        url = f"https://guba.eastmoney.com/list,{code}.html"

        time.sleep(0.3)  # rate limit
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        html = resp.content.decode("utf-8", errors="replace")

        # The page embeds post data as: var article_list={...};
        match = re.search(r"var article_list=(\{.*?\});", html, re.DOTALL)
        if not match:
            logger.warning(f"No article_list found in guba HTML for {ticker}")
            return _fallback_guba(ticker)

        data = json.loads(match.group(1))
        posts = data.get("re", []) if isinstance(data, dict) else []

        if not posts:
            return _fallback_guba(ticker)

        lines = []
        for post in posts[:limit]:
            title = post.get("post_title", "").strip()
            read_count = post.get("post_click_count", 0)
            comment_count = post.get("post_comment_count", 0)
            time_str = post.get("post_publish_time", "")
            if title:
                lines.append(
                    f"- **{title}** | 阅读: {read_count} | 评论: {comment_count} | {time_str}"
                )

        if not lines:
            return _fallback_guba(ticker)

        header = f"## 东方财富股吧帖子 — {ticker}\n"
        header += f"共 {len(lines)} 条帖子\n\n"
        return header + "\n".join(lines)

    except Exception as e:
        logger.warning(f"Guba fetch failed for {ticker}: {e}")
        return _fallback_guba(ticker)


def _fallback_guba(ticker: str) -> str:
    """Try akshare's stock_comment_em as fallback for sentiment data."""
    try:
        import akshare as ak
        code = extract_a_share_code(ticker)
        time.sleep(0.3)
        df = ak.stock_comment_em()
        if df is not None and not df.empty:
            # Filter for our stock
            row = df[df['代码'] == code] if '代码' in df.columns else df.head(0)
            if not row.empty:
                r = row.iloc[0]
                lines = [f"## 股票评论数据 — {ticker}\n"]
                for col in df.columns:
                    val = r[col]
                    if pd.notna(val):
                        lines.append(f"- {col}: {val}")
                return "\n".join(lines)
    except Exception as e:
        logger.warning(f"akshare stock_comment_em fallback also failed for {ticker}: {e}")

    return (
        f"股吧数据暂时无法获取（{ticker}）。"
        "东方财富股吧可能有反爬限制，建议参考其他数据源。"
    )


def analyze_guba_sentiment(posts_text: str) -> Dict:
    """Simple keyword-based sentiment analysis on Guba post titles.

    Returns: {'score': float, 'positive': int, 'negative': int, 'summary': str}
    """
    if not posts_text:
        return {'score': 0.0, 'positive': 0, 'negative': 0, 'summary': '无数据'}

    positive_count = sum(1 for w in _POSITIVE_WORDS if w in posts_text)
    negative_count = sum(1 for w in _NEGATIVE_WORDS if w in posts_text)
    total = positive_count + negative_count

    if total == 0:
        score = 0.0
    else:
        score = (positive_count - negative_count) / total

    if score > 0.3:
        summary = '散户情绪明显偏多'
    elif score > 0.1:
        summary = '散户情绪略偏多'
    elif score > -0.1:
        summary = '散户情绪中性'
    elif score > -0.3:
        summary = '散户情绪略偏空'
    else:
        summary = '散户情绪明显偏空'

    return {
        'score': round(score, 3),
        'positive': positive_count,
        'negative': negative_count,
        'summary': summary,
    }
