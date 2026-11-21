"""Anthropic 提示词缓存边界声明器（Prompt Cache Boundary）。

设计动机与不变式：
Skill / Webhook / Cron 调度器经常将巨大的静态脚手架（Scaffold）与微小的易变动态尾部（Tail）
拼接为单条 User 消息。仅有构建器知道静态前缀与动态尾部的切分点。
因此，构建器将稳定静态前缀注册到本模块，缓存规划器（Cache Planner）即可精准在边界放置断点。

【核心不变式】：
1. **避免运行时重新标记解析**：严禁在请求时通过特殊分隔符二次扫描（分隔符可能合法存在于技能体中，
   易导致前缀误缩减或污染易变字节）；
2. **非空尾部约束**：前缀切分后的 Tail 必须包含非空白字符，防止生成空 TextBlock 导致 Anthropic HTTP 400 拒绝；
3. **LRU 缓存保活**：高频 Cron 脚手架在命中时刷新 LRU，防止被突发一次性技能调用驱逐。
"""

import threading
from collections import OrderedDict
from typing import Optional

# A couple dozen active scaffolds is generous for one gateway process.
_MAX_ENTRIES = 32
# Entries hold whole expanded skill bodies, so also bound total retained chars.
# The newest entry is always kept so one oversized scaffold still gets a boundary.
_MAX_CHARS = 4 * 1024 * 1024

_lock = threading.Lock()
_prefixes: OrderedDict[str, None] = OrderedDict()


def register_stable_prefix(prefix: str) -> None:
    """将 ``prefix`` 注册为刚构建消息的稳定静态脚手架（线程安全）。"""
    if not prefix:
        return
    with _lock:
        _prefixes[prefix] = None
        _prefixes.move_to_end(prefix)
        while len(_prefixes) > _MAX_ENTRIES:
            _prefixes.popitem(last=False)
        while len(_prefixes) > 1 and sum(map(len, _prefixes)) > _MAX_CHARS:
            _prefixes.popitem(last=False)


def find_stable_prefix(content: str) -> Optional[str]:
    """在 ``content`` 中查找已注册的最长稳定真前缀（要求尾部包含非空白字符）。

    尾部必须包含非空白字符，确保切分后的动态部分不会变为空文本块
    （Anthropic 会以 HTTP 400 拒绝空文本块）。命中时刷新 LRU 位置。"""
    with _lock:
        best: Optional[str] = None
        for prefix in _prefixes:
            if (
                content.startswith(prefix)
                and content[len(prefix):].strip()
                and (best is None or len(prefix) > len(best))
            ):
                best = prefix
        if best is not None:
            _prefixes.move_to_end(best)  # after the scan: never mutate mid-iteration
        return best
