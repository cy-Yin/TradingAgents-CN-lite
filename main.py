import os
import sys

# Fix Windows console encoding for emoji output from langchain
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from generate_report import generate_html_report, generate_html_report_cn

# DEFAULT_CONFIG already applies TRADINGAGENTS_* env-var overrides
# (llm_provider, deep_think_llm, quick_think_llm, backend_url, etc.),
# so users can switch models or endpoints purely via .env without
# editing this script. Override individual keys here only when you
# want a hard-coded value that should ignore the environment.
config = DEFAULT_CONFIG.copy()

# Initialize with custom config
ta = TradingAgentsGraph(debug=True, config=config)

# forward propagate
ticker = "AAPL"
trade_date = "2026-05-22"

final_state, decision = ta.propagate(ticker, trade_date)
print(f"\n{'='*60}")
print(f"Decision: {decision}")
print(f"{'='*60}\n")

# Build markdown report from final_state
debate = final_state.get("investment_debate_state", {})
risk = final_state.get("risk_debate_state", {})

md = f"""# {ticker} Investment Analysis Report
> Date: {trade_date} | Rating: **{decision}**

## I. Technical Analysis (Market Analyst)

{final_state.get('market_report', 'N/A')}

## II. Sentiment Analysis (Sentiment Analyst)

{final_state.get('sentiment_report', 'N/A')}

## III. News Analysis (News Analyst)

{final_state.get('news_report', 'N/A')}

## IV. Fundamentals Analysis (Fundamentals Analyst)

{final_state.get('fundamentals_report', 'N/A')}

## V. Investment Debate

### Bull Case
{debate.get('bull_history', 'N/A')}

### Bear Case
{debate.get('bear_history', 'N/A')}

### Debate Conclusion
{debate.get('judge_decision', 'N/A')}

## VI. Research Manager Investment Plan

{final_state.get('investment_plan', 'N/A')}

## VII. Trader Proposal

{final_state.get('trader_investment_plan', 'N/A')}

## VIII. Risk Debate

### Aggressive Analyst
{risk.get('aggressive_history', 'N/A')}

### Conservative Analyst
{risk.get('conservative_history', 'N/A')}

### Neutral Analyst
{risk.get('neutral_history', 'N/A')}

### Risk Conclusion
{risk.get('judge_decision', 'N/A')}

## IX. Final Portfolio Decision

{final_state.get('final_trade_decision', 'N/A')}
"""

# Save markdown report
reports_dir = os.path.join(os.path.dirname(__file__), "reports")
os.makedirs(reports_dir, exist_ok=True)
md_filename = f"{ticker}_{trade_date}_report.md"
md_path = os.path.join(reports_dir, md_filename)

with open(md_path, "w", encoding="utf-8") as f:
    f.write(md)
print(f"Markdown report saved to: {md_path}")

# Generate HTML reports (English + Chinese)
html_path = generate_html_report(md_path)
print(f"HTML report saved to: {html_path}")

html_cn_path = generate_html_report_cn(md_path)
print(f"Chinese HTML report saved to: {html_cn_path}")

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000) # parameter is the position returns
