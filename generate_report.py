"""Read a TradingAgents markdown report and generate an investment-bank-grade HTML."""

import os
import sys

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI


SYSTEM_PROMPT = """You are a senior equity research report designer at Goldman Sachs.

Task: Read the markdown investment analysis report below. Then produce a SINGLE, self-contained HTML report — concise, visual, authoritative. This is NOT a markdown-to-HTML conversion. You must ANALYZE the content and present only the key insights in a clean, scannable layout.

## Design System

**Colors (Goldman Sachs):**
- Header bg: #003A70 (deep blue)
- Gold accent: #B59A57 (borders, highlights, rating badge outline)
- Buy green: #1B5E20 | Sell red: #B71C1C | Hold gold: #B59A57
- Card bg: #FAFAF8 | Card border: #E8E6E1
- Body text: #1A1A1A | Secondary: #6B6B6B
- Section divider: 1px solid #E8E6E1

**Fonts (Google Fonts @import in <style>):**
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400&display=swap');
- Headings: 'Playfair Display', serif
- Body: 'Inter', sans-serif
- Numbers/data: 'JetBrains Mono', monospace

**Layout Rules:**
- Max-width 860px, centered, generous whitespace
- NO walls of text. Use cards, badges, grids, tables
- Rating as a large centered badge at top (Buy=green, Hold=gold, Sell=red)
- Executive summary: 3-5 bullet points max, in a gold-bordered card
- Key metrics in a compact grid (PE, Market Cap, Price Target, etc.)
- Bull vs Bear: side-by-side cards (green left, red right), 3-5 points each
- Risk section: compact card grid
- Final decision: prominent card with clear recommendation
- Print-friendly, proper page breaks

**Content Rules:**
- Present the FULL analysis substantively — each section should have detailed, data-backed insights, not just bullet-point summaries.
- Keep all important reasoning, metrics, data points, and conclusions. Only cut truly redundant text, repetitive filler, and raw data dumps (like full CSV tables).
- Each section should read like a real equity research note: 2-4 well-written paragraphs with specific numbers, dates, and supporting evidence.
- Bull/Bear debate: include the key arguments with their supporting reasoning, not just headline points.
- Risk section: detail each risk factor with context, not just labels.
- Executive summary: comprehensive overview (5-8 sentences), not a skeleton.
- ALL content MUST be in English. If the source markdown is Chinese, translate everything into English. Technical terms (AAPL, RSI, MACD) stay as-is.
- Professional header with ticker + date + rating.
- Subtle footer with disclaimer.

Output ONLY the complete HTML file starting with <!DOCTYPE html>. No markdown fences, no explanation."""


CN_SYSTEM_PROMPT = """你是高盛级别的中文投行报告设计师。

任务：阅读下方 markdown 投资分析报告，生成一份简洁、直观、专业的中文 HTML 报告。这不是翻译——你需要提炼关键信息，用投行报告的视觉语言呈现。

## 设计规范

**配色（高盛风格）：**
- 页眉背景：#003A70（深蓝）
- 金色点缀：#B59A57（边框、高亮、评级徽章描边）
- 买入绿：#1B5E20 | 卖出红：#B71C1C | 持有金：#B59A57
- 卡片背景：#FAFAF8 | 卡片边框：#E8E6E1
- 正文：#1A1A1A | 次要文字：#6B6B6B

**字体（Google Fonts @import + 系统字体）：**
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400&display=swap');
- 标题：'Microsoft YaHei', 'PingFang SC', sans-serif — 微软雅黑，干净专业
- 正文：'Microsoft YaHei', 'PingFang SC', sans-serif
- 数据/数字：'JetBrains Mono', monospace

**内容规则：**
- 完整呈现分析内容——每节应有详实、数据支撑的洞察，不是简单的要点罗列。
- 保留所有重要推理、指标、数据点和结论。只删减真正冗余的重复文字和原始数据表。
- 每节应像真实的股票研究报告：2-4 段有具体数字、日期和证据的分析文字。
- 多空辩论：包含关键论点及其支撑逻辑，不只是标题。
- 风险评估：详述每个风险因素的背景，不只是标签。
- 执行摘要：5-8 句话的全面概述，不是骨架式的提纲。
- 章节用中文序号：一、二、三...
- 专业术语首次出现附英文：市盈率（P/E Ratio）
- 评级用中文：买入、增持、持有、减持、卖出
- 日期格式：2026年5月22日

**布局：**
- 最大宽度 860px，居中，留白充足
- 评级大徽章居中展示（买入=绿、持有=金、卖出=红）
- 执行摘要：3-5 条要点，金色边框卡片
- 核心指标：紧凑网格（PE、市值、目标价等）
- 多空对比：左右卡片（绿左红右），各 3-5 条
- 风险评估：卡片网格
- 最终决策：醒目卡片，明确建议
- 打印友好

纯 HTML + 内联 CSS，单文件。仅允许 Google Fonts @import。只输出完整 HTML，以 <!DOCTYPE html> 开头。
- 所有内容必须为中文。若源报告为英文，需翻译为中文。"""


def _call_llm(md_content: str, system_prompt: str) -> str:
    """Call LLM to generate HTML from markdown content."""
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("TRADINGAGENTS_LLM_BACKEND_URL", "https://api.openai.com/v1"),
    )

    response = client.chat.completions.create(
        model=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "gpt-4o"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": md_content},
        ],
        temperature=0.3,
        max_tokens=131072,
    )

    html_content = response.choices[0].message.content.strip()
    if html_content.startswith("```"):
        html_content = html_content.split("\n", 1)[1]
    if html_content.endswith("```"):
        html_content = html_content.rsplit("```", 1)[0]
    return html_content.strip()


def generate_html_report(md_path: str) -> str:
    """Generate English HTML report from markdown. Returns path to HTML file."""
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    print("[generate_report] Generating English HTML report...")
    html_content = _call_llm(md_content, SYSTEM_PROMPT)

    html_path = md_path.rsplit(".", 1)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[generate_report] English HTML saved to: {html_path}")
    return html_path


def generate_html_report_cn(md_path: str) -> str:
    """Generate Chinese HTML report from markdown. Returns path to HTML file."""
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    print("[generate_report] Generating Chinese HTML report...")
    html_content = _call_llm(md_content, CN_SYSTEM_PROMPT)

    html_path = md_path.rsplit(".", 1)[0] + "_cn.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[generate_report] Chinese HTML saved to: {html_path}")
    return html_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python generate_report.py <path_to_report.md>")
        sys.exit(1)
    md_path = sys.argv[1]
    generate_html_report(md_path)
    generate_html_report_cn(md_path)
