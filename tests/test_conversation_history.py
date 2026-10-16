"""
ConversationHistory 单元测试（P1-5）

测试策略：
- 纯 assert，不依赖任何测试框架；可直接 python3 运行，也兼容 pytest
- 通过文件路径直接加载被测模块（模块本身只依赖标准库），
  避免触发 voice_chatbot.dialogue/__init__ -> manager -> numpy 等重依赖链
- 通过注入 now_fn（FakeClock）精确控制合并窗口

运行方式：
    python3 tests/test_conversation_history.py
    或
    pytest tests/test_conversation_history.py
"""

import importlib.util
import os

# ---------- 隔离加载被测模块（不经过包 __init__，规避重依赖链） ----------
_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "voice_chatbot", "dialogue", "conversation_history.py",
)
_spec = importlib.util.spec_from_file_location("conversation_history_isolated", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ConversationHistory = _mod.ConversationHistory


class FakeClock:
    """可手动推进的时钟（注入 now_fn 用）"""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float):
        self.now += dt


def _make(**kwargs):
    """默认带 FakeClock 的工厂，便于各用例覆盖参数"""
    kwargs.setdefault("max_records", 50)
    clock = kwargs.pop("clock", None)
    if "now_fn" not in kwargs:
        kwargs["now_fn"] = clock if clock is not None else FakeClock()
    return ConversationHistory(**kwargs)


# ---------- 用例 ----------

def test_merge_within_window():
    """窗口内的连续用户输入应原位合并为一条（VAD切分场景）"""
    h = _make(max_records=10, merge_window_s=10.0)
    r1 = h.record_user("今天天气", prev_turn_audible=False)
    r2 = h.record_user("怎么样", prev_turn_audible=False)
    assert r1 == "今天天气"
    assert r2 == "今天天气，怎么样", "窗口内应合并并返回合并文本"
    assert len(h) == 1
    snap = h.snapshot_all()
    assert snap == [{"role": "user", "content": "今天天气，怎么样"}]


def test_merge_strips_trailing_punct_of_previous():
    """合并时应去掉上一条末尾的逗号/句号等标点再拼接"""
    h = _make()
    h.record_user("你好。", prev_turn_audible=False)
    merged = h.record_user("世界", prev_turn_audible=False)
    assert merged == "你好，世界"


def test_no_merge_after_window_expired():
    """超过合并窗口的输入视为新的话轮，正常追加"""
    clock = FakeClock()
    h = _make(clock=clock, merge_window_s=10.0)
    h.record_user("第一句", prev_turn_audible=False)
    clock.advance(10.0)  # 恰好到达窗口边界（判定条件为严格小于），不合并
    r2 = h.record_user("第二句", prev_turn_audible=False)
    assert r2 == "第二句"
    assert len(h) == 2
    roles = [m["role"] for m in h.snapshot_all()]
    assert roles == ["user", "user"]


def test_no_merge_when_prev_turn_audible():
    """上一话轮机器人播出过声音（独立话轮）时，窗口内也不合并"""
    clock = FakeClock()
    h = _make(clock=clock, merge_window_s=10.0)
    h.record_user("第一句", prev_turn_audible=False)
    h.record_assistant("这是回复")
    clock.advance(1.0)
    r2 = h.record_user("第二句", prev_turn_audible=True)
    assert r2 == "第二句"
    assert len(h) == 3
    assert [m["role"] for m in h.snapshot_all()] == ["user", "assistant", "user"]


def test_max_merged_chars_cap():
    """合并后超过 max_merged_chars 上限则视为两次独立输入，正常追加"""
    h = _make(max_merged_chars=200)
    first = "甲" * 150
    second = "乙" * 100  # 合并后 150+1+100=251 > 200
    h.record_user(first, prev_turn_audible=False)
    r2 = h.record_user(second, prev_turn_audible=False)
    assert r2 == second, "超上限不应合并，返回原文本"
    assert len(h) == 2
    snap = h.snapshot_all()
    assert snap[0]["content"] == first and snap[1]["content"] == second


def test_max_merged_chars_boundary_merges():
    """合并后恰好等于上限时仍应合并（判定条件为 <=）"""
    h = _make(max_merged_chars=200)
    first = "甲" * 99   # 99 + 1(逗号) + 100 = 200
    second = "乙" * 100
    h.record_user(first, prev_turn_audible=False)
    merged = h.record_user(second, prev_turn_audible=False)
    assert merged == first + "，" + second
    assert len(merged) == 200
    assert len(h) == 1


def test_assistant_resets_merge_window():
    """assistant 记录后合并窗口应关闭：紧接着的连续输入不与更早的user合并"""
    clock = FakeClock()
    h = _make(clock=clock, merge_window_s=10.0)
    h.record_user("第一句", prev_turn_audible=False)
    assert h.record_assistant("回复一") is True
    clock.advance(1.0)  # 仍在原窗口时间内，但assistant已重置窗口
    r2 = h.record_user("第二句", prev_turn_audible=False)
    assert r2 == "第二句"
    assert len(h) == 3
    assert [m["role"] for m in h.snapshot_all()] == ["user", "assistant", "user"]


def test_interrupted_ellipsis_suffix():
    """被打断且不以句子结束符结尾的回复应追加省略号截断标记（移植 state.py 原实现）"""
    h = _make()
    assert h.record_assistant("我说到一半就", interrupted=True) is True
    assert h.snapshot_all()[-1]["content"] == "我说到一半就..."


def test_interrupted_full_sentence_no_suffix():
    """被打断但以句子结束符结尾的完整句子不加截断标记（原实现的完整句子判断）"""
    h = _make()
    h.record_assistant("已经说完了。", interrupted=True)
    assert h.snapshot_all()[-1]["content"] == "已经说完了。"
    h.record_assistant("完整答复！", interrupted=True)
    assert h.snapshot_all()[-1]["content"] == "完整答复！"


def test_normal_assistant_no_suffix_and_lstrip():
    """未被打断的回复不加标记；前导空白应被去除；空文本跳过"""
    h = _make()
    assert h.record_assistant("  正常回复") is True
    assert h.snapshot_all()[-1]["content"] == "正常回复"
    assert h.record_assistant("") is False
    assert h.record_assistant("   ") is False
    assert h.record_assistant(None) is False
    assert len(h) == 1


def test_pending_input_excluded_by_identity():
    """待定当前输入应按对象身份从 snapshot_for_llm 排除（含合并后仍正确排除）"""
    h = _make()
    h.record_user("你好", prev_turn_audible=False)
    assert h.snapshot_for_llm() == [], "刚录入的当前输入不应出现在LLM历史中"
    assert len(h.snapshot_all()) == 1, "snapshot_all 应包含待定输入"

    # 窗口内合并：最后一条 dict 被原位修改（身份不变），排除依然正确
    merged = h.record_user("世界", prev_turn_audible=False)
    assert merged == "你好，世界"
    assert h.snapshot_for_llm() == [], "合并后（同一dict对象）仍应被身份排除"
    assert h.snapshot_all() == [{"role": "user", "content": "你好，世界"}]

    # assistant 回复消费掉待定输入后，两者都进入LLM历史
    h.record_assistant("回复", interrupted=False)
    llm_history = h.snapshot_for_llm()
    assert llm_history == [
        {"role": "user", "content": "你好，世界"},
        {"role": "assistant", "content": "回复"},
    ]


def test_pending_exclusion_survives_independent_append():
    """超上限追加（不合并）产生新dict时，排除的是新的待定输入，旧的user保留"""
    h = _make(max_merged_chars=10)
    h.record_user("一二三四五六七八九十", prev_turn_audible=False)  # 10字
    r2 = h.record_user("新的独立输入", prev_turn_audible=False)     # 合并会超10字
    assert r2 == "新的独立输入"
    llm_history = h.snapshot_for_llm()
    assert llm_history == [{"role": "user", "content": "一二三四五六七八九十"}]


def test_clear():
    """clear 应原位清空并复位窗口/待定标记；clear后窗口逻辑仍正常工作"""
    clock = FakeClock()
    h = _make(clock=clock)
    h.record_user("你好", prev_turn_audible=False)
    h.record_assistant("回复")
    assert len(h) == 2
    h.clear()
    assert len(h) == 0
    assert h.snapshot_all() == []
    assert h.snapshot_for_llm() == []

    # clear后功能不受影响：新一轮录入+窗口内合并正常
    r1 = h.record_user("新话题", prev_turn_audible=False)
    clock.advance(1.0)
    r2 = h.record_user("继续说", prev_turn_audible=False)
    assert r1 == "新话题"
    assert r2 == "新话题，继续说"
    assert len(h) == 1


def test_trim_keeps_latest_and_stays_live():
    """裁剪到上限后保留最新记录；对外快照始终反映最新状态（防P0-1类冻结回归）

    用对外可见行为断言：provider/快照是"每次调用都读取当前状态"的活视图，
    连续记录100+条后，早先取得的快照不变、新快照包含新记录且长度==上限。
    """
    clock = FakeClock()
    h = _make(clock=clock, max_records=5)  # 上限 = 5轮 = 10条消息
    provider = h.snapshot_for_llm  # 模拟LLM侧的 history_provider

    h.record_user("u0", prev_turn_audible=False)
    h.record_assistant("a0")
    early_all = h.snapshot_all()
    early_llm = provider()
    # u0已被assistant消费（待定标记清除），两条都进入LLM历史
    assert len(early_all) == 2 and len(early_llm) == 2

    # 连续记录 60 轮（120条），远超上限
    for i in range(1, 61):
        h.record_user(f"u{i}", prev_turn_audible=False)
        h.record_assistant(f"a{i}")

    assert len(h) == 10, "应裁剪到 max_records*2 条"

    late_all = h.snapshot_all()
    late_llm = provider()
    # 早先快照保持当时的样子（快照语义），且与内部状态无身份耦合
    assert early_all == [{"role": "user", "content": "u0"}, {"role": "assistant", "content": "a0"}]
    assert early_llm == [{"role": "user", "content": "u0"}, {"role": "assistant", "content": "a0"}]
    assert late_all is not early_all

    # 新快照未被冻结：包含最新记录，且裁剪保留的是最新的10条
    assert late_all[-2]["content"] == "u60" and late_all[-1]["content"] == "a60"
    assert late_all[0]["content"] == "u56", "应裁掉最旧的记录（u56/a56之前）"
    roles = [m["role"] for m in late_all]
    assert roles == ["user", "assistant"] * 5

    # 循环以assistant结尾，待定输入已被消费 -> LLM快照包含全部10条，以a60结尾
    assert len(late_llm) == 10
    assert late_llm[-1]["content"] == "a60"

    # 修改快照副本不影响内部状态（副本dict/副本列表）
    late_all.append({"role": "user", "content": "HACK"})
    late_all[0]["content"] = "HACKED"
    assert len(h) == 10
    fresh = h.snapshot_all()
    assert fresh[0]["content"] == "u56" and len(fresh) == 10

    # clear后长度归零（内部列表原位清空，无rebind残留）
    h.clear()
    assert len(h) == 0 and provider() == []


def test_merge_after_trim_not_with_trimmed_record():
    """裁剪发生后，合并只能与现存最后一条user合并（对象已换，不误并旧记录）"""
    clock = FakeClock()
    h = _make(clock=clock, max_records=3)  # 上限6条
    for i in range(3):
        h.record_user(f"u{i}", prev_turn_audible=False)
        h.record_assistant(f"a{i}")
    # 此时6条已满；新的user输入追加并裁掉最旧的u0/a0
    r = h.record_user("新输入", prev_turn_audible=False)
    assert r == "新输入"
    assert len(h) == 6
    contents = [m["content"] for m in h.snapshot_all()]
    assert contents == ["a0", "u1", "a1", "u2", "a2", "新输入"]


def test_record_user_returns_effective_text():
    """record_user 返回值：合并时返回合并文本，追加时返回原文本"""
    clock = FakeClock()
    h = _make(clock=clock)
    assert h.record_user("你好", prev_turn_audible=False) == "你好"
    assert h.record_user("世界", prev_turn_audible=False) == "你好，世界"
    clock.advance(100.0)
    assert h.record_user("新句子", prev_turn_audible=False) == "新句子"


# ---------- 直接运行入口（不依赖 pytest） ----------

def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[ERROR] {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
