#!/usr/bin/env python3
"""Convert value analysis markdown report to self-contained mobile-friendly HTML.
Preserves ALL original content — no parsing, no restructuring, no content loss.
"""

import argparse
import markdown

CSS = """
:root {
  --bg: #fafaf7; --bg2: #f0efe9; --text: #1c1c1a; --text2: #5c5c58;
  --text3: #8a8a84; --border: rgba(0,0,0,.08); --green: #1a7a5a;
  --green-bg: #e6f4ee; --red: #c0392b; --red-bg: #fceaea;
  --amber: #a06c1a; --amber-bg: #faf0d8; --blue: #2563a0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141414; --bg2: #1e1e1e; --text: #e8e8e4; --text2: #a8a8a0;
    --text3: #6e6e68; --border: rgba(255,255,255,.08); --green: #4ade80;
    --green-bg: rgba(74,222,128,.1); --red: #f87171; --red-bg: rgba(248,113,113,.1);
    --amber: #fbbf24; --amber-bg: rgba(251,191,36,.1); --blue: #60a5fa;
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', system-ui, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.8; font-size: 16px;
  max-width: 720px; margin: 0 auto; padding: 24px 20px 80px;
}
h1 { font-size: 22px; margin: 8px 0 4px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px;
     color: var(--text3); margin: 42px 0 14px; padding-bottom: 8px;
     border-bottom: 1px solid var(--border); }
h3 { font-size: 16px; margin: 24px 0 10px; }
h4 { font-size: 14px; color: var(--text2); margin: 16px 0 8px; }
p { margin: 12px 0; color: var(--text2); }
p strong { color: var(--text); }
blockquote {
  border-left: 3px solid var(--border); padding: 6px 16px;
  margin: 14px 0; color: var(--text3); font-style: italic;
}
ul, ol { margin: 10px 0 14px 22px; color: var(--text2); }
li { margin: 5px 0; }
table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }
th { text-align: left; padding: 8px 10px; font-size: 11px; color: var(--text3);
     text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--border); }
th:not(:first-child) { text-align: right; }
td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
td:not(:first-child) { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
hr { border: none; border-top: 1px solid var(--border); margin: 28px 0; }
a { color: var(--blue); }
code { font-size: 13px; background: var(--bg2); padding: 2px 6px; border-radius: 4px; }
pre { background: var(--bg2); padding: 14px; border-radius: 8px; overflow-x: auto;
      font-size: 13px; margin: 14px 0; }
.disclaimer {
  margin-top: 48px; padding: 16px; background: var(--bg2); border-radius: 8px;
  font-size: 12px; color: var(--text3); line-height: 1.7;
}
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
{body}
<div class="disclaimer">{disclaimer}</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="MD → mobile HTML")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Extract title from first H1
    title = "价值分析报告"
    for line in md_text.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break

    body_html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )

    # Pull disclaimer into its own div
    disclaimer = (
        "本报告基于公开信息生成，不构成投资建议。"
        "投资有风险，决策须谨慎。"
    )
    disc_pattern = '<p><em>本报告基于'
    disc_idx = body_html.find(disc_pattern)
    if disc_idx > 0:
        # Find the closing </p> of the disclaimer
        end_idx = body_html.find("</p>", disc_idx)
        disc_html = body_html[disc_idx:end_idx + 4]
        body_html = body_html[:disc_idx] + body_html[end_idx + 4:]
        # strip <p> tags
        disc_clean = disc_html.replace("<p>", "").replace("</p>", "").replace("<em>", "").replace("</em>", "")
        disclaimer = disc_clean.strip()

    html = HTML_TEMPLATE.format(
        title=title,
        css=CSS.strip(),
        body=body_html,
        disclaimer=disclaimer,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"OK: {args.output}")


if __name__ == "__main__":
    main()
