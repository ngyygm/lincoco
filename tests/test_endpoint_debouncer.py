"""
EndpointDebouncer 单元测试 + manager 终句debounce接线测试（P3）

测试策略：
- 第一部分：通过文件路径直接加载 endpoint_debouncer.py（模块本身只依赖标准库），
  避免触发 voice_chatbot.dialogue/__init__ -> manager -> numpy 等重依赖链；
  用注入的假计时器/假时钟测试，不产生任何真实等待
- 第二部分：沿用 test_interrupt_p2.py 的桩注入方式（pygame/pydub/pyaudio 桩模块），
  构建不经过 initialize_modules 的 DialogueManager 验证接线：
  非打断流final进入debounce并合并、SPEAKING/已确认打断/debounce=0绕过立即处理、
  partial/VAD重新开始说话取消计时保留缓冲、stop()/clear_conversation_history()清理

运行方式：
    python3 tests/test_endpoint_debouncer.py
    或
    pytest tests/test_endpoint_debouncer.py
"""

import importlib.util
import os
import sys
import threading
import types

# ======================================================================
# 第一部分：EndpointDebouncer 隔离单元测试（假计时器/假时钟，无真实等待）
# ======================================================================

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "voice_chatbot", "dialogue", "endpoint_debouncer.py",
)
_spec = importlib.util.spec_from_file_location("endpoint_debouncer_isolated", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
EndpointDebouncer = _mod.EndpointDebouncer


class FakeTimer:
    """假计时器：记录interval/回调/状态；fire()同步模拟计时到期（等同timer线程执行）"""

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        """模拟计时自然到期：直接执行回调（threading.Timer语义，cancel迟到也照样执行，
        由debouncer内部的代数机制负责淘汰过期触发）"""
        if not self.started:
            raise AssertionError("计时器尚未start就被fire")
        self.function()


class FakeTimerFactory:
    """假计时器工厂：记录全部创建的计时器（最后一个即当前pending计时）"""

    def __init__(self):
        self.created = []

    def __call__(self, interval, function):
        timer = FakeTimer(interval, function)
        self.created.append(timer)
        return timer


class FakeClock:
    """假时钟：可注入advance，驱动max_gap_s过期判定"""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def latest_timer(factory):
    """取最近创建的计时器（即当前pending计时）"""
    assert factory.created, "应当已创建计时器"
    return factory.created[-1]


def test_offer_merge_and_fire_once():
    """连续offer合并缓冲，重置计时，静默到期只触发一次"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)

    deb.offer("我想问一下")
    assert deb.is_pending(), "offer后应处于pending状态"
    timer1 = latest_timer(factory)
    assert timer1.interval == 0.5, "计时器间隔应等于delay_s"

    deb.offer("今天天气")
    timer2 = latest_timer(factory)
    assert timer2 is not timer1, "重新offer应创建新计时器"
    assert timer1.cancelled, "重新offer应取消旧计时器"

    timer2.fire()  # 模拟静默窗口到期
    assert fired == ["我想问一下，今天天气"], f"应合并为一句触发: {fired}"
    assert not deb.is_pending(), "触发后不应再pending"
    assert deb.pending_text() == "", "触发后缓冲应清空"

    timer2.fire()  # 迟到的重复触发（竞态模拟）
    assert fired == ["我想问一下，今天天气"], "同一计时器重复触发不应二次派发"
    print("[PASS] 连续offer合并缓冲，到期只触发一次")


def test_cancel_prevents_fire_and_keeps_buffer():
    """cancel()不触发回调且保留缓冲（后续final可并入）"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)

    deb.offer("你好")
    timer = latest_timer(factory)
    deb.cancel()
    assert not deb.is_pending(), "cancel后不应pending"
    assert deb.pending_text() == "你好", "cancel应保留缓冲（不清空）"

    timer.fire()  # 模拟cancel迟到的竞态：回调已进入timer线程
    assert fired == [], "被cancel的触发不应派发（代数淘汰）"

    # 缓冲保留：新final并入后重新计时
    deb.offer("世界")
    latest_timer(factory).fire()
    assert fired == ["你好，世界"], f"cancel保留的缓冲应与新offer合并: {fired}"
    print("[PASS] cancel不触发且保留缓冲")


def test_reset_drops_timer_and_buffer():
    """reset()取消计时并清空缓冲，返回被丢弃文本（stop/清空历史路径）"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)

    deb.offer("旧碎片")
    timer = latest_timer(factory)
    dropped = deb.reset()
    assert dropped == "旧碎片", "reset应返回被丢弃的缓冲文本"
    assert not deb.is_pending() and deb.pending_text() == ""

    timer.fire()
    assert fired == [], "reset后迟到的触发不应派发"

    deb.offer("新话轮")
    latest_timer(factory).fire()
    assert fired == ["新话轮"], "reset后缓冲干净，不应混入旧碎片"
    print("[PASS] reset取消计时并清空缓冲")


def test_delay_zero_equivalent():
    """delay=0：offer同步立即触发、不创建计时器、不合并（与未启用debounce行为一致）"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0, fired.append, timer_factory=factory)

    deb.offer("你好")
    assert fired == ["你好"], "delay=0应在offer内同步触发"
    deb.offer("世界")
    assert fired == ["你好", "世界"], "delay=0每次offer独立触发（无合并、无缓冲）"
    assert factory.created == [], "delay=0不应创建任何计时器"
    assert not deb.is_pending()
    print("[PASS] delay=0行为等价（同步触发/无计时器/无合并）")


def test_stale_generation_race_no_dispatch():
    """被替换的旧计时器竞态迟到触发：代数不匹配，不作废新状态"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)

    deb.offer("A")
    timer1 = latest_timer(factory)
    deb.offer("B")  # timer1被替换
    timer1.fire()   # 模拟timer1的cancel竞态迟到（自然到期执行）
    assert fired == [], "被替换的旧计时器触发不应派发"

    latest_timer(factory).fire()
    assert fired == ["A，B"], f"新计时器正常派发合并文本: {fired}"
    print("[PASS] 代数机制淘汰被替换计时的迟到触发")


def test_empty_offer_ignored():
    """空白文本不进入缓冲、不创建计时器"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)

    deb.offer("   ")
    deb.offer("")
    deb.offer(None)
    assert factory.created == [] and not deb.is_pending()
    assert deb.pending_text() == ""
    print("[PASS] 空白offer被忽略")


def test_max_gap_staleness_drop():
    """距上次offer超过max_gap_s的旧缓冲被丢弃（防陈旧碎片并入新话轮）"""
    fired = []
    clock = FakeClock(1000.0)
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, now_fn=clock,
                            timer_factory=factory, max_gap_s=10.0)

    # 窗口内间隔（5秒 <= max_gap_s=10）：缓冲保留，新offer并入合并
    deb.offer("第一句")
    clock.advance(5.0)
    deb.offer("第二句")
    latest_timer(factory).fire()
    assert fired == ["第一句，第二句"], f"窗口内应合并: {fired}"

    # 超期间隔（30秒 > max_gap_s=10）：旧缓冲丢弃，不并入新话轮
    deb.offer("旧碎片")
    clock.advance(30.0)
    deb.offer("新话轮")
    latest_timer(factory).fire()
    assert fired == ["第一句，第二句", "新话轮"], f"超期旧缓冲应丢弃: {fired}"
    print("[PASS] max_gap_s过期丢弃/窗口内保留")


def test_thread_safety_smoke():
    """多线程并发offer/cancel/查询不异常，最终状态自洽（假计时器，无真实等待）"""
    fired = []
    factory = FakeTimerFactory()
    deb = EndpointDebouncer(0.5, fired.append, timer_factory=factory)
    errors = []

    def hammer_offer(worker_id):
        try:
            for k in range(300):
                deb.offer(f"t{worker_id}-{k}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def hammer_cancel_and_query():
        try:
            for _ in range(300):
                deb.cancel()
                deb.is_pending()
                deb.pending_text()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def hammer_fire():
        try:
            for _ in range(300):
                timers = list(factory.created)
                if timers:
                    timers[-1].fire()  # 只触发当前pending计时（等同timer线程）
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=hammer_offer, args=(i,)) for i in range(3)]
    threads += [threading.Thread(target=hammer_cancel_and_query) for _ in range(2)]
    threads += [threading.Thread(target=hammer_fire)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发操作不应抛异常: {errors}"
    # 终态自洽：reset后缓冲与计时干净
    deb.reset()
    assert not deb.is_pending() and deb.pending_text() == ""
    print("[PASS] 多线程并发smoke（offer/cancel/查询/触发无异常）")


# ======================================================================
# 第二部分：manager 终句debounce 接线测试（沿用P2桩注入方式）
# ======================================================================

# ---------- 隔离注入桩模块（必须在导入 voice_chatbot 之前） ----------
for _name in ("pygame", "pygame.mixer", "pydub", "pyaudio"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
sys.modules["pygame"].mixer = sys.modules["pygame.mixer"]
sys.modules["pydub"].AudioSegment = object
sys.modules["pyaudio"].PyAudio = object

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import voice_chatbot.dialogue.manager as manager_mod  # noqa: E402
from voice_chatbot.dialogue.events import EventType, create_event  # noqa: E402
from voice_chatbot.dialogue.state import SubSystemState  # noqa: E402

DialogueManager = manager_mod.DialogueManager


class StubConfig:
    """最小配置桩：仅支持 get(点分路径, 默认值)，未配置的键走调用方默认值"""

    def __init__(self, values=None):
        self.values = values or {}

    def get(self, path, default=None):
        cur = self.values
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur


def fresh_manager(values=None):
    """构建一个不经过 initialize_modules 的 DialogueManager（模块实例均为轻量对象）"""
    return DialogueManager(StubConfig(values))


def wired_manager(values=None):
    """构建接线测试用manager：running=True + 假计时器debouncer + 拦截推理流程

    Returns:
        (mgr, factory, flows): mgr=manager实例，factory=假计时器工厂，
        flows=已触发"记录+处理"流程的user_input列表（以_start_inference_flow拦截）
    """
    mgr = fresh_manager(values)
    mgr.running = True  # 使debounce到期回调不被running=False兜底拦截
    flows = []
    mgr._start_inference_flow = lambda context: flows.append(context.user_input)
    factory = FakeTimerFactory()
    mgr._endpoint_debouncer = EndpointDebouncer(
        delay_s=mgr._endpoint_debounce_delay_s,
        fire_callback=mgr._on_endpoint_debounce_fire,
        timer_factory=factory,
    )
    return mgr, factory, flows


def test_config_default_and_json_sync():
    """P3-1：endpoint_debounce_ms 默认500，config.py默认值与config.json同步"""
    from voice_chatbot.utils.config import Config

    # manager侧读取（未配置时走代码默认500）
    assert fresh_manager()._endpoint_debounce_delay_s == 0.5, "默认应为500ms"
    assert fresh_manager({"asr": {"endpoint_debounce_ms": 0}})._endpoint_debounce_delay_s == 0.0
    assert fresh_manager({"asr": {"endpoint_debounce_ms": 1200}})._endpoint_debounce_delay_s == 1.2

    # 真实 config.json（项目根目录，自动加载）
    c = Config()
    assert c.get("asr.endpoint_debounce_ms") == 500, "config.json 缺少 asr.endpoint_debounce_ms=500"

    # config.py DEFAULT_CONFIG（不存在文件路径 -> 纯默认值）
    dc = Config(config_file=os.path.join(_PROJECT_ROOT, "tests", "_nonexistent_config.json"))
    assert dc.get("asr.endpoint_debounce_ms") == 500, "config.py 默认值应为500"

    # 校验拒绝负值
    bad = Config(config_file=os.path.join(_PROJECT_ROOT, "tests", "_nonexistent_config.json"))
    bad.config["asr"]["endpoint_debounce_ms"] = -1
    try:
        bad.validate()
        raise AssertionError("负值endpoint_debounce_ms应校验失败")
    except AssertionError as exc:
        if "必须大于等于0" not in str(exc):
            raise
    print("[PASS] endpoint_debounce_ms 默认500且config.py/config.json同步")


def test_final_debounced_and_merged():
    """非打断流final进入debounce缓冲，到期合并触发一次完整处理"""
    mgr, factory, flows = wired_manager()

    mgr._on_asr_final_result("我想问一下")
    assert mgr._endpoint_debouncer.is_pending(), "final应进入debounce缓冲"
    assert not flows, "debounce期间不应启动处理流程"
    assert len(mgr.history) == 0, "debounce期间不应写入对话历史"

    mgr._on_asr_final_result("今天天气怎么样")
    assert len(factory.created) == 2, "新final应重启计时"
    factory.created[-1].fire()  # 模拟静默窗口到期

    assert flows == ["我想问一下，今天天气怎么样"], f"应合并触发一次处理: {flows}"
    assert not mgr._endpoint_debouncer.is_pending()
    records = mgr.history.snapshot_all()
    assert len(records) == 1 and records[0]["role"] == "user", "应恰好记录一条合并后的用户输入"
    assert records[0]["content"] == "我想问一下，今天天气怎么样"
    print("[PASS] 非打断流final进入debounce并合并触发")


def test_speaking_bypasses_debounce():
    """机器人SPEAKING（打断流）绕过debounce立即处理，且丢弃pending缓冲"""
    mgr, factory, flows = wired_manager()
    mgr._endpoint_debouncer.offer("旧碎片")  # 模拟此前挂起的pending缓冲
    mgr.state_manager.transition_sub_state(SubSystemState.SPEAKING, "测试：播放中")
    timers_before = len(factory.created)

    mgr._on_asr_final_result("别说了呀")
    assert flows == ["别说了呀"], "SPEAKING打断流应绕过debounce立即处理"
    assert not mgr._endpoint_debouncer.is_pending(), "打断流不应产生pending计时"
    assert mgr._endpoint_debouncer.pending_text() == "", "打断流应丢弃pending缓冲（不回流后续话轮）"
    assert len(factory.created) == timers_before, "打断流不应创建新的终句计时器"
    print("[PASS] SPEAKING打断流绕过debounce立即处理")


def test_confirmed_interrupt_bypasses_debounce():
    """已确认打断（final未消化）时绕过debounce立即处理，保证打断延迟"""
    mgr, factory, flows = wired_manager()
    ctx = mgr.context_manager.create_context("机器人回复中")
    mgr.interrupted_task_id = ctx.task_id  # 模拟已确认打断、final尚未处理
    assert mgr._in_interrupt_semantics() is True

    mgr._on_asr_final_result("换个话题聊聊")
    assert flows == ["换个话题聊聊"], "已确认打断的final应立即处理"
    assert not mgr._endpoint_debouncer.is_pending()
    assert factory.created == []

    # 误判恢复等待窗口（播放被暂停等待final判定）同样属于打断语义
    mgr2, _, flows2 = wired_manager()
    mgr2._interrupt_paused_task_id = 999
    assert mgr2._in_interrupt_semantics() is True
    mgr2._on_asr_final_result("继续说")
    assert flows2 == ["继续说"]
    print("[PASS] 已确认打断绕过debounce立即处理")


def test_debounce_zero_immediate():
    """endpoint_debounce_ms=0：完全绕过debounce，行为与旧版一致"""
    mgr, factory, flows = wired_manager({"asr": {"endpoint_debounce_ms": 0}})

    mgr._on_asr_final_result("你好呀")
    assert flows == ["你好呀"], "debounce=0应立即处理"
    assert factory.created == [], "debounce=0不应创建任何计时器"
    assert not mgr._endpoint_debouncer.is_pending()
    assert len(mgr.history) == 1, "应立即写入对话历史"
    print("[PASS] debounce=0完全绕过（行为与旧版一致）")


def test_empty_final_not_buffered():
    """空final不进入缓冲（保持旧的空结果处理路径：不启动流程、可触发误判恢复）"""
    mgr, factory, flows = wired_manager()

    mgr._on_asr_final_result("")
    assert not mgr._endpoint_debouncer.is_pending(), "空final不应进入缓冲"
    assert mgr._endpoint_debouncer.pending_text() == ""
    assert flows == [], "空结果不应启动处理流程"
    assert factory.created == []
    print("[PASS] 空final不进入debounce缓冲")


def test_partial_resets_timer_keeps_buffer():
    """窗口内收到新的partial：取消计时保留缓冲，新final并入后重新计时"""
    mgr, factory, flows = wired_manager()

    mgr._on_asr_final_result("我想问一下")
    assert mgr._endpoint_debouncer.is_pending()

    event = create_event(
        EventType.ASR_PARTIAL_RESULT,
        data={"task_id": 1, "partial_text": "嗯嗯", "confidence": None},
        source="ASR",
    )
    mgr._on_asr_partial_result(event)
    assert not mgr._endpoint_debouncer.is_pending(), "partial应取消终句计时"
    assert mgr._endpoint_debouncer.pending_text() == "我想问一下", "缓冲应保留待新final并入"

    mgr._on_asr_final_result("今天天气")
    factory.created[-1].fire()
    assert flows == ["我想问一下，今天天气"], "partial后的新final应并入缓冲合并触发"
    print("[PASS] partial取消计时保留缓冲，新final并入")


def test_vad_speech_start_cancels_timer():
    """VAD重新检测到语音：取消计时保留缓冲，新final并入合并"""
    mgr, factory, flows = wired_manager()
    mgr._start_asr_thread = lambda: None  # 避免测试路径启动真实ASR线程

    mgr._on_asr_final_result("我想问一下")
    assert mgr._endpoint_debouncer.is_pending()

    mgr._on_vad_speech_start()
    assert not mgr._endpoint_debouncer.is_pending(), "VAD重新开始说话应取消终句计时"
    assert mgr._endpoint_debouncer.pending_text() == "我想问一下", "缓冲应保留"

    mgr._on_asr_final_result("怎么样")
    factory.created[-1].fire()
    assert flows == ["我想问一下，怎么样"]
    print("[PASS] VAD重新开始说话取消计时保留缓冲")


def test_stop_cancels_and_flushes_debounce():
    """stop()取消pending计时并清空缓冲，关闭后触发不再启动管线"""
    mgr, factory, flows = wired_manager()

    mgr._on_asr_final_result("待处理碎片")
    assert mgr._endpoint_debouncer.is_pending()

    mgr.stop()
    assert not mgr._endpoint_debouncer.is_pending(), "stop后不应pending"
    assert mgr._endpoint_debouncer.pending_text() == "", "stop应清空缓冲"

    factory.created[-1].fire()  # 模拟stop竞态迟到的触发
    assert flows == [], "系统停止后debounce触发不应启动处理管线"
    print("[PASS] stop取消计时清空缓冲，关闭后不触发管线")


def test_clear_history_flushes_debounce():
    """clear_conversation_history()清空pending缓冲，碎片不回流污染新历史"""
    mgr, factory, flows = wired_manager()

    mgr._on_asr_final_result("待处理碎片")
    assert mgr._endpoint_debouncer.is_pending()

    mgr.clear_conversation_history()
    assert not mgr._endpoint_debouncer.is_pending()
    assert mgr._endpoint_debouncer.pending_text() == ""

    factory.created[-1].fire()  # 模拟清空后迟到的触发
    assert flows == [], "清空历史后pending碎片不应再触发处理"
    assert len(mgr.history) == 0
    print("[PASS] 清空对话历史同时清空debounce缓冲")


# ---------- 入口 ----------
def main():
    test_offer_merge_and_fire_once()
    test_cancel_prevents_fire_and_keeps_buffer()
    test_reset_drops_timer_and_buffer()
    test_delay_zero_equivalent()
    test_stale_generation_race_no_dispatch()
    test_empty_offer_ignored()
    test_max_gap_staleness_drop()
    test_thread_safety_smoke()
    test_config_default_and_json_sync()
    test_final_debounced_and_merged()
    test_speaking_bypasses_debounce()
    test_confirmed_interrupt_bypasses_debounce()
    test_debounce_zero_immediate()
    test_empty_final_not_buffered()
    test_partial_resets_timer_keeps_buffer()
    test_vad_speech_start_cancels_timer()
    test_stop_cancels_and_flushes_debounce()
    test_clear_history_flushes_debounce()
    print("\n全部 P3 单元测试通过")


if __name__ == "__main__":
    main()
