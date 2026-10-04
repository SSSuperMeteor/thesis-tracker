"""CLI entry for one DeepSeek-driven Decision Mode analysis."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from thesis_tracker.decision.agent import DeepSeekClient, run_analysis
from thesis_tracker.decision.core import DEFAULT_ARCHIVE


def main(argv: list[str] | None = None, *, client: Any = None,
         archive_path: Path | str = DEFAULT_ARCHIVE) -> int:
    parser = argparse.ArgumentParser(description="Analyze one ticker with DeepSeek Decision Mode")
    parser.add_argument("ticker")
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        active_client = client if client is not None else DeepSeekClient()
        result = run_analysis(args.ticker, args.as_of, client=active_client,
                              archive_path=archive_path)
    except ValueError as exc:
        print(f"拒绝：{type(exc).__name__}；请检查标的、日期和 DEEPSEEK_API_KEY 配置。")
        return 1
    stats = result["stats"]
    print(f"分析 {args.ticker.upper()}｜{args.as_of}｜{result['status']}")
    print(f"工具调用 {stats['tool_calls']} 次｜对话 {stats['rounds']} 轮｜修正 {stats['revisions']} 次")
    print("实际工具：" + ("、".join(stats["tools"]) if stats["tools"] else "无"))
    print(f"token 输入 {stats['input_tokens']}｜输出 {stats['output_tokens']}｜缓存命中 {stats['cache_hit_tokens']}")
    if result["status"] == "passed":
        print(result["rendered"])
        print(f"存档编号：{result['card_id']}")
        return 0
    print(f"拒绝原因：{result['reason']}")
    for item in result["violations"]:
        print(f"- {item['rule']} {item['location']}：{item['message']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
