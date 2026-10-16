"""
P2 打断决策/误判恢复/配置整理 单元测试（隔离，不导入重依赖链）

测试策略：
- 通过 sys.modules 预注入 pygame/pydub/pyaudio 桩模块，使 manager 的导入链
  在无音频依赖的环境下可用（工作流环境无 pygame/pyaudio）
- 纯 assert，不依赖任何测试框架；可直接 python3 运行，也兼容 pytest
- 覆盖：_should_real_interrupt 四条件（有效性/最小字数/置信度/冷却）、
  _validate_input 配置化（backchannel_chars/枚举词）、_confirm_real_interrupt
  暂停语义（pause替代stop+延迟历史记录）、_recover_misclassified_interrupt、
  _process_user_input 新任务硬停止、_handle_asr_result 轻量falsy检查、
  _on_asr_partial_result 事件门控（confidence字段+按任务去重）、
  config.py 默认值与 config.json 同步

运行方式：
    python3 tests/test_interrupt_p2.py
    或
    pytest tests/test_interrupt_p2.py
"""

import sys
import types
import os
import importlib.util

# ---------- 隔离注入桩模块（必须在导入 voice_chatbot 之前） ----------
for _name in ("pygame", "pygame.mixer", "pydub", "pyaudio"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
# audio_player.py 依赖 pygame.mixer / pydub.AudioSegment（仅类型标注与运行时调用）
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


# ---------- 测试辅助 ----------
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


class FakePlayer:
    """音频播放器桩：记录 pause/resume/stop 调用"""

    def __init__(self, current_task_id=None):
        self._tid = current_task_id
        self.paused = False
        self.pause_calls = []
        self.resume_calls = []
        self.stop_calls = []

    def get_current_task_id(self):
        return self._tid

    def pause(self, task_id=None):
        self.pause_calls.append(task_id)
        if not self.paused:
            self.paused = True
            return True
        return False

    def resume(self, task_id=None):
        self.resume_calls.append(task_id)
        if self.paused:
            self.paused = False
            return True
        return False

    def stop(self, task_id=None):
        self.stop_calls.append(task_id)
        self.paused = False
        return True

    def is_playing(self):
        return not self.paused


def fresh_manager(values=None):
    """构建一个不经过 initialize_modules 的 DialogueManager（模块实例均为轻量对象）"""
    mgr = DialogueManager(StubConfig(values))
    return mgr


def reset_cooldown(mgr):
    """清零冷却计时，保证各用例独立"""
    mgr._last_real_interrupt_time = 0.0


# ---------- P2-3：config 默认值与 config.json 同步 ----------
def test_config_defaults_and_json_sync():
    from voice_chatbot.utils.config import Config

    # 真实 config.json（项目根目录，自动加载）
    c = Config()
    assert c.get("interrupt.min_text_chars") == 2, "config.json 缺少 interrupt.min_text_chars=2"
    assert c.get("interrupt.min_confidence") == 0.3, "config.json 缺少 interrupt.min_confidence=0.3"
    assert c.get("interrupt.cooldown_seconds") == 1.0, "config.json 缺少 interrupt.cooldown_seconds=1.0"
    assert c.get("interrupt.recover_on_misclassify") is True, "config.json 缺少 interrupt.recover_on_misclassify=true"
    assert c.get("asr.backchannel_chars") == "嗯啊哦呃额噢唉呀哈嘿诶欸唔", "config.json 缺少 asr.backchannel_chars"

    words = c.get("asr.invalid_recognition_words")
    for w in ["嗯哼", "嘻嘻", "呵呵", "嗯呐", "啊这", "对对对", "好的好的", "好好好"]:
        assert w in words, f"枚举词 '{w}' 未并入 config.json 的 asr.invalid_recognition_words"
    for dead in ["嗯。", "啊。", "呃。", "哦。", "额。"]:
        assert dead not in words, f"死条目 '{dead}' 应从 config.json 删除（归一化已覆盖）"

    # config.py DEFAULT_CONFIG（不存在文件路径 -> 纯默认值）
    dc = Config(config_file=os.path.join(_PROJECT_ROOT, "tests", "_nonexistent_config.json"))
    assert dc.get("interrupt.min_text_chars") == 2
    assert dc.get("interrupt.min_confidence") == 0.3
    assert dc.get("interrupt.cooldown_seconds") == 1.0
    assert dc.get("interrupt.recover_on_misclassify") is True
    assert dc.get("asr.backchannel_chars") == "嗯啊哦呃额噢唉呀哈嘿诶欸唔"
    dwords = dc.get("asr.invalid_recognition_words")
    for w in ["嗯哼", "对对对", "好的好的", "好好好"]:
        assert w in dwords, f"枚举词 '{w}' 未并入 config.py 默认 asr.invalid_recognition_words"
    print("[PASS] config 默认值与 config.json 同步")


# ---------- P2-1：_should_real_interrupt 四条件 ----------
def test_should_real_interrupt_conditions():
    mgr = fresh_manager()

    # 条件1：有效性（无效词/纯附和词不触发）
    for text in ["", "嗯", "嗯嗯", "啊哦", "i", "嗯哼"]:
        reset_cooldown(mgr)
        assert mgr._should_real_interrupt(text) is False, f"'{text}' 不应构成打断"

    # 条件2：最小有效字符数（默认2，单字应答不触发）
    for text in ["对", "好", "对。"]:
        reset_cooldown(mgr)
        assert mgr._should_real_interrupt(text) is False, f"单字 '{text}' 不应构成打断"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("好的呀") is True, "两字以上有效输入应构成打断"

    # 条件3：置信度（None/<=0 放行；否则 >= 0.3）
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", None) is True, "置信度缺失(None)应放行"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.0) is True, "置信度<=0应放行"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", -1.0) is True, "负置信度应放行"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.1) is False, "低置信度不应打断"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.3) is True, "达到min_confidence应打断"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.99) is True, "高置信度应打断"

    # 条件4：跨任务冷却（默认1.0秒）
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀") is True
    assert mgr._last_real_interrupt_time > 0, "判定为True时应更新冷却计时"
    assert mgr._should_real_interrupt("别说了呀") is False, "冷却期内不应重复打断"
    mgr._last_real_interrupt_time -= 5.0  # 人为回拨，模拟冷却结束
    assert mgr._should_real_interrupt("别说了呀") is True, "冷却结束后应恢复打断"

    print("[PASS] _should_real_interrupt 四条件判定")


def test_should_real_interrupt_configurable():
    # 最小字符数可配置
    mgr = fresh_manager({"interrupt": {"min_text_chars": 3}})
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀"[0:2]) is False, "min_text_chars=3 时两字不应打断"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀") is True

    # 置信度阈值可配置
    mgr = fresh_manager({"interrupt": {"min_confidence": 0.8}})
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.5) is False, "min_confidence=0.8 时0.5不应打断"
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀", 0.85) is True

    # 冷却时长可配置
    mgr = fresh_manager({"interrupt": {"cooldown_seconds": 100.0}})
    reset_cooldown(mgr)
    assert mgr._should_real_interrupt("你好呀") is True
    mgr._last_real_interrupt_time -= 50.0
    assert mgr._should_real_interrupt("你好呀") is False, "cooldown=100s 时50s后仍在冷却期"
    print("[PASS] _should_real_interrupt 阈值全部可配置")


# ---------- P2-3：_validate_input 配置化 ----------
def test_validate_input_config_driven():
    mgr = fresh_manager()
    # 纯附和词字符（asr.backchannel_chars 驱动，替代硬编码 _BACKCHANNEL_CHARS）
    for text in ["嗯嗯", "啊哦", "嗯哈", "哦噢", "唉呀嘿"]:
        assert mgr._validate_input(text) is False, f"纯附和词 '{text}' 应无效"
    # 枚举词（asr.invalid_recognition_words 驱动，替代硬编码 _BACKCHANNEL_WORDS）
    for text in ["嗯哼", "嘻嘻", "呵呵", "嗯呐", "啊这", "对对对", "好的好的", "好好好"]:
        assert mgr._validate_input(text) is False, f"枚举词 '{text}' 应无效（配置表驱动）"
    # 归一化覆盖标点变体
    for text in ["嗯。", "啊！", "对对对。", "好的好的，", "嗯哼？"]:
        assert mgr._validate_input(text) is False, f"标点变体 '{text}' 应无效（归一化匹配）"
    # 有效输入（注意："I"归一化为"i"，在无效词表中，属既有行为）
    for text in ["你好", "嗯，我想问一下", "AI助手", " hello "]:
        assert mgr._validate_input(text) is True, f"'{text}' 应有效"
    assert mgr._validate_input(" I ") is False, "'I'归一化为'i'应无效（既有无效词）"
    assert not hasattr(DialogueManager, "_BACKCHANNEL_CHARS"), "_BACKCHANNEL_CHARS 硬编码应删除"
    assert not hasattr(DialogueManager, "_BACKCHANNEL_WORDS"), "_BACKCHANNEL_WORDS 硬编码应删除"
    print("[PASS] _validate_input 配置化（backchannel_chars/枚举词入配置）")


# ---------- P2-2：_confirm_real_interrupt 暂停语义 ----------
def test_confirm_real_interrupt_pauses_instead_of_stop():
    mgr = fresh_manager()
    ctx = mgr.context_manager.create_context("测试回复")
    mgr.state_manager.transition_sub_state(SubSystemState.SPEAKING, "测试：播放中")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake

    mgr._confirm_real_interrupt()

    assert len(fake.pause_calls) == 1, "SPEAKING打断应调用pause()而非stop()"
    assert len(fake.stop_calls) == 0, "打断确认路径不应再硬stop（硬停止移至新任务路径）"
    assert mgr._interrupt_paused_task_id == ctx.task_id, "应记录待恢复任务ID"
    assert not ctx.get_extra_data("history_recorded_by_interrupt", False), "暂停语义下不应抢先写历史标志"
    assert len(mgr.history) == 0, "暂停语义下不应抢先记录历史（延迟到终态）"
    assert mgr.state_manager.get_sub_state() == SubSystemState.NONE, "打断确认后副系统应转为NONE"
    assert mgr.interrupted_task_id == ctx.task_id, "应记录已打断任务（按任务去重）"

    # 同一任务重复确认：按任务去重，不再二次暂停
    mgr.state_manager.transition_sub_state(SubSystemState.SPEAKING, "测试：再次播放中")
    mgr._confirm_real_interrupt()
    assert len(fake.pause_calls) == 1, "同一任务重复打断应被去重"
    print("[PASS] _confirm_real_interrupt 暂停语义（pause+延迟历史记录+按任务去重）")


# ---------- P2-2：误判恢复 ----------
def test_recover_misclassified_interrupt_resumes():
    mgr = fresh_manager()
    ctx = mgr.context_manager.create_context("测试回复")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake
    fake.paused = True  # 模拟已被打断确认暂停
    mgr._interrupt_paused_task_id = ctx.task_id

    mgr._recover_misclassified_interrupt("测试：final无效")

    assert len(fake.resume_calls) == 1, "误判恢复应调用resume()续播"
    assert len(fake.stop_calls) == 0, "误判恢复不应stop"
    assert mgr._interrupt_paused_task_id is None, "恢复后应清除待恢复标记"
    assert mgr.state_manager.get_sub_state() == SubSystemState.SPEAKING, "恢复后副系统应回到SPEAKING"

    # 标记已消费：再次调用为no-op
    mgr._recover_misclassified_interrupt("测试：再次final无效")
    assert len(fake.resume_calls) == 1, "无可恢复暂停时应为no-op"
    print("[PASS] 误判恢复：resume续播 + 状态回SPEAKING + 标记只消费一次")


def test_recover_disabled_stops_paused_playback():
    mgr = fresh_manager({"interrupt": {"recover_on_misclassify": False}})
    ctx = mgr.context_manager.create_context("测试回复")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake
    fake.paused = True
    mgr._interrupt_paused_task_id = ctx.task_id

    mgr._recover_misclassified_interrupt("测试：final无效")

    assert len(fake.stop_calls) == 1, "关闭恢复时应硬停止被暂停的播放（旧行为）"
    assert len(fake.resume_calls) == 0, "关闭恢复时不应resume"
    assert mgr._interrupt_paused_task_id is None
    print("[PASS] 误判恢复关闭：硬停止被暂停的播放")


def test_recover_skips_when_task_replaced():
    mgr = fresh_manager()
    mgr.context_manager.create_context("新任务")
    fake = FakePlayer(current_task_id=12345)
    mgr.audio_player = fake
    mgr._interrupt_paused_task_id = 999  # 已被新任务取代的旧任务

    mgr._recover_misclassified_interrupt("测试：final无效")

    assert len(fake.resume_calls) == 0, "任务已被取代时不应resume（播放已随之终止）"
    assert len(fake.stop_calls) == 0
    assert mgr._interrupt_paused_task_id is None, "标记仍应被消费"
    print("[PASS] 误判恢复：任务已被取代时安全跳过")


# ---------- P2-2：_process_user_input 新任务硬停止 + 防御检查恢复 ----------
def test_process_user_input_hard_stops_paused_playback():
    mgr = fresh_manager()
    ctx = mgr.context_manager.create_context("旧任务回复")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake
    fake.paused = True  # 打断确认路径只做了暂停
    mgr._interrupt_paused_task_id = ctx.task_id

    flows = []
    mgr._start_inference_flow = lambda context: flows.append(context.task_id)

    mgr._process_user_input("讲个故事吧")

    assert len(fake.stop_calls) == 1, "新任务路径应硬停止被暂停的旧播放"
    assert mgr._interrupt_paused_task_id is None, "新任务路径应清除待恢复标记"
    assert len(flows) == 1, "有效输入应正常启动推理流程"
    assert mgr.context_manager.get_current_task_id() != ctx.task_id, "应创建新任务"
    print("[PASS] _process_user_input 新任务路径硬停止 + 清除恢复标记")


def test_process_user_input_defensive_check_triggers_recovery():
    mgr = fresh_manager()
    ctx = mgr.context_manager.create_context("旧任务回复")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake
    fake.paused = True
    mgr._interrupt_paused_task_id = ctx.task_id

    flows = []
    mgr._start_inference_flow = lambda context: flows.append(context.task_id)

    mgr._process_user_input("嗯哼")  # 无效但非空：轻量falsy检查放行，防御检查拦截

    assert len(flows) == 0, "无效输入不应启动推理流程"
    assert len(fake.resume_calls) == 1, "防御检查拦截后应触发误判恢复（resume续播）"
    assert mgr._interrupt_paused_task_id is None
    assert mgr.context_manager.get_current_task_id() == ctx.task_id, "不应创建新任务"
    print("[PASS] _process_user_input 防御检查兜底拦截 + 触发误判恢复")


# ---------- P2-4：_handle_asr_result 轻量falsy检查 ----------
def test_handle_asr_result_lightweight_check():
    mgr = fresh_manager()
    captured = []
    mgr._process_user_input = lambda text: captured.append(text)
    mgr._recover_misclassified_interrupt = lambda reason: captured.append(("recover", reason))

    mgr._handle_asr_result("")
    assert captured == [("recover", "最终识别结果为空")], "空文本应提前return并触发恢复"

    mgr._handle_asr_result("你好呀")
    assert captured[-1] == "你好呀", "非空文本应交给_process_user_input"
    # 无效但非空文本不被_handle_asr_result拦截（由_process_user_input防御检查兜底）
    mgr._handle_asr_result("嗯哼")
    assert captured[-1] == "嗯哼", "轻量falsy检查应放行无效非空文本（三重验证收敛）"
    print("[PASS] _handle_asr_result 轻量falsy检查 + 恢复钩子")


# ---------- P2-1：_on_asr_partial_result 事件门控 ----------
def test_on_asr_partial_result_event_gating():
    mgr = fresh_manager()
    ctx = mgr.context_manager.create_context("正在播放的任务")
    mgr.state_manager.transition_sub_state(SubSystemState.SPEAKING, "测试：播放中")
    fake = FakePlayer(current_task_id=ctx.task_id)
    mgr.audio_player = fake

    def fire(partial_text, confidence):
        event = create_event(
            EventType.ASR_PARTIAL_RESULT,
            data={"task_id": ctx.task_id, "partial_text": partial_text, "confidence": confidence},
            source="ASR",
        )
        mgr._on_asr_partial_result(event)

    reset_cooldown(mgr)
    fire("嗯", None)          # 无效词：不触发
    fire("对", None)          # 单字：不触发（min_text_chars=2）
    fire("嗯嗯呀", None)      # 纯附和词：不触发
    fire("你好呀", 0.1)       # 低置信度：不触发
    assert len(fake.pause_calls) == 0, "以上用例均不应触发打断"

    fire("你好呀", None)      # 有效+置信度缺失：触发
    assert len(fake.pause_calls) == 1, "有效partial应确认打断（暂停播放）"
    assert mgr._interrupt_paused_task_id == ctx.task_id
    assert mgr.interrupted_task_id == ctx.task_id

    # 按任务去重：同任务后续partial不再打断
    fire("你好呀是什么", None)
    assert len(fake.pause_calls) == 1, "同一任务应按任务去重"

    # 冷却期：新任务也不触发（跨任务冷却）
    mgr.context_manager.create_context("新任务")
    mgr.state_manager.transition_sub_state(SubSystemState.SPEAKING, "测试：新任务播放中")
    fake2 = FakePlayer(current_task_id=mgr.context_manager.get_current_task_id())
    mgr.audio_player = fake2

    def fire2(partial_text, confidence):
        event = create_event(
            EventType.ASR_PARTIAL_RESULT,
            data={"task_id": mgr.context_manager.get_current_task_id(),
                  "partial_text": partial_text, "confidence": confidence},
            source="ASR",
        )
        mgr._on_asr_partial_result(event)

    fire2("等等等等", None)
    assert len(fake2.pause_calls) == 0, "冷却期内跨任务也不应打断"
    print("[PASS] _on_asr_partial_result 事件门控（confidence字段+去重+冷却）")


# ---------- 入口 ----------
def main():
    test_config_defaults_and_json_sync()
    test_should_real_interrupt_conditions()
    test_should_real_interrupt_configurable()
    test_validate_input_config_driven()
    test_confirm_real_interrupt_pauses_instead_of_stop()
    test_recover_misclassified_interrupt_resumes()
    test_recover_disabled_stops_paused_playback()
    test_recover_skips_when_task_replaced()
    test_process_user_input_hard_stops_paused_playback()
    test_process_user_input_defensive_check_triggers_recovery()
    test_handle_asr_result_lightweight_check()
    test_on_asr_partial_result_event_gating()
    print("\n全部 P2 单元测试通过")


if __name__ == "__main__":
    main()
