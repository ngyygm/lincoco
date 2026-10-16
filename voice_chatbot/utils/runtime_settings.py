"""
运行时热设置模块

定义可从 Web 端热修改的运行时参数注册表（每项带中文简短说明），
提供读取当前状态与应用变更的统一入口：

- 大多数参数在代码中以 config.get() 实时读取，写回 Config 即热生效
- 少数参数在初始化时固化到对象字段，apply 时额外同步（见 _apply_special）
- 全部变更写回 config.json 持久化，重启后保持

Web 协议（WebSocket 文本消息）：
- 请求 {type: "get_settings"}                  → 回执 {type: "settings_state", settings: [...]}
- 下发 {type: "set_settings", changes: {path: value, ...}} → 回执 {type: "settings_result", ...}
  应用成功后向所有客户端广播最新 settings_state
"""

from typing import Any, Dict, List, Optional

# 可热修改参数注册表（顺序即 Web 端展示顺序）
# type: int / float / bool / multiselect
RUNTIME_SETTINGS: List[Dict[str, Any]] = [
    # ===== 打断判定 =====
    {
        "path": "interrupt.min_text_chars", "group": "打断判定",
        "label": "最小打断字数", "type": "int", "min": 1, "max": 20, "step": 1,
        "desc": "构成真打断所需的最小有效字数（1=单字'对/好'也可打断，调大更不易误触发）",
    },
    {
        "path": "interrupt.min_confidence", "group": "打断判定",
        "label": "打断置信度门槛", "type": "float", "min": 0.0, "max": 1.0, "step": 0.05,
        "desc": "ASR识别置信度低于该值不判打断（0=不检查置信度）",
    },
    {
        "path": "interrupt.cooldown_seconds", "group": "打断判定",
        "label": "打断冷却间隔", "type": "float", "min": 0.0, "max": 10.0, "step": 0.5,
        "desc": "两次真打断之间的最小间隔秒数，防止连续误触发",
    },
    {
        "path": "interrupt.recover_on_misclassify", "group": "打断判定",
        "label": "误判自动恢复", "type": "bool",
        "desc": "开启：打断后识别结果无效时自动续播（假打断）；关闭：一律停止播放",
    },
    # ===== 识别间隔 =====
    {
        "path": "asr.no_speech_threshold", "group": "识别间隔",
        "label": "无语音超时", "type": "float", "min": 0.2, "max": 10.0, "step": 0.1,
        "desc": "多久没有语音活动就结束本轮识别并出结果（秒）",
    },
    {
        "path": "asr.endpoint_debounce_ms", "group": "识别间隔",
        "label": "终句静默窗口", "type": "int", "min": 0, "max": 3000, "step": 100,
        "desc": "说完话后等多久才算一句结束（毫秒）：说话停顿的碎片会合并成一句再处理，0=关闭合并",
    },
    {
        "path": "asr.input_merge_window_seconds", "group": "识别间隔",
        "label": "输入合并窗口", "type": "float", "min": 0.0, "max": 60.0, "step": 1.0,
        "desc": "VAD切分的连续碎片在该秒数内并入同一条历史记录",
    },
    {
        "path": "asr.max_merged_input_chars", "group": "识别间隔",
        "label": "合并字符上限", "type": "int", "min": 10, "max": 1000, "step": 10,
        "desc": "合并后的最大字符数，超过则视为两次独立输入",
    },
    # ===== VAD 灵敏度 =====
    {
        "path": "vad.speech_threshold", "group": "VAD 灵敏度",
        "label": "语音判定比例", "type": "float", "min": 0.1, "max": 0.9, "step": 0.05,
        "desc": "检测窗口内多大比例的帧有语音才判为'正在说话'，调大抗噪、调小更灵敏",
    },
    {
        "path": "vad.mode", "group": "VAD 灵敏度",
        "label": "VAD 灵敏度等级", "type": "int", "min": 0, "max": 3, "step": 1,
        "desc": "WebRTC VAD 模式 0-3，数字越大越容易判为语音（3=最敏感）",
    },
    {
        "path": "vad.check_interval", "group": "VAD 灵敏度",
        "label": "VAD 检测间隔", "type": "float", "min": 0.1, "max": 2.0, "step": 0.1,
        "desc": "VAD 轮询检测的时间间隔（秒），越小反应越快、CPU 占用越高",
    },
    # ===== 对话上下文 =====
    {
        "path": "llm.max_history_length", "group": "对话上下文",
        "label": "历史轮数上限", "type": "int", "min": 2, "max": 200, "step": 2,
        "desc": "LLM 可见的最大对话历史轮数（每轮=用户+助手两条）",
    },
    # ===== 控制台日志 =====
    {
        "path": "logging.show_source", "group": "控制台日志",
        "label": "显示日志来源", "type": "bool",
        "desc": "在每条日志中显示产生日志的源文件名（仅影响服务端控制台）",
    },
    {
        "path": "logging.filtered_levels", "group": "控制台日志",
        "label": "输出级别", "type": "multiselect",
        "options": ["INFO", "DEBUG", "WARN", "ERROR"],
        "desc": "服务端控制台输出的日志级别（全不选=全部输出）",
    },
    {
        "path": "logging.filtered_categories", "group": "控制台日志",
        "label": "输出类别", "type": "multiselect",
        "options": ["ASR", "TTS", "LLM", "PLAY", "MAIN", "MANAGER", "TIME", "WS", "VAD",
                    "CTX", "PUN", "SPE", "EVENT", "THREAD", "PERSONA"],
        "desc": "服务端控制台输出的日志类别白名单（全不选=全部输出）",
    },
]

_SETTING_PATHS = {s["path"] for s in RUNTIME_SETTINGS}


def get_settings_state(config) -> List[Dict[str, Any]]:
    """读取全部热设置项的当前值（含描述），用于 Web 端渲染设置面板"""
    state = []
    for setting in RUNTIME_SETTINGS:
        item = dict(setting)
        item["value"] = config.get(setting["path"])
        state.append(item)
    return state


def _coerce(setting: Dict[str, Any], value: Any) -> Any:
    """按注册表类型转换/校验 Web 端传入的值，非法则抛 ValueError"""
    t = setting["type"]
    if t == "int":
        value = int(value)
    elif t == "float":
        value = float(value)
    elif t == "bool":
        if not isinstance(value, bool):
            raise ValueError("需要 true/false")
    elif t == "multiselect":
        options = set(setting.get("options", []))
        if not isinstance(value, list) or any(v not in options for v in value):
            raise ValueError(f"取值必须是 {sorted(options)} 的子集")
        return value
    if "min" in setting and value < setting["min"]:
        raise ValueError(f"不能小于 {setting['min']}")
    if "max" in setting and value > setting["max"]:
        raise ValueError(f"不能大于 {setting['max']}")
    return value


def _apply_special(manager, path: str, value: Any) -> None:
    """
    同步初始化时固化到对象字段的参数（其余参数 config.get 实时读取，无需处理）
    """
    # 终句debounce：manager 缓存的秒值 + debouncer 的计时窗口
    if path == "asr.endpoint_debounce_ms":
        manager._endpoint_debounce_delay_s = max(0.0, float(value)) / 1000.0
        manager._endpoint_debouncer.set_delay(manager._endpoint_debounce_delay_s)
    # 输入合并窗口：历史记录器 + debouncer 的缓冲保留间隔
    elif path == "asr.input_merge_window_seconds":
        manager.history.merge_window_s = float(value)
        manager._endpoint_debouncer.set_max_gap(float(value))
    elif path == "asr.max_merged_input_chars":
        manager.history.max_merged_chars = int(value)
    elif path == "llm.max_history_length":
        manager.history.max_records = int(value)
    # 无语音超时：manager 字段（识别结束判定用）
    elif path == "asr.no_speech_threshold":
        manager.no_speech_threshold = float(value)
    # VAD 灵敏度：webrtcvad 实例需重设 mode；threshold/间隔为循环实时读取
    elif path == "vad.mode":
        manager.vad.vad_mode = int(value)
        if manager.vad.vad is not None:
            manager.vad.vad.set_mode(int(value))
    elif path == "vad.speech_threshold":
        manager.vad.speech_threshold = float(value)
    elif path == "vad.check_interval":
        manager.vad.check_interval = float(value)
    # 控制台日志：热更新全局 logger
    elif path in ("logging.filtered_levels", "logging.filtered_categories"):
        from voice_chatbot.utils.logger import update_logger_filter
        update_logger_filter(manager.config)
    elif path == "logging.show_source":
        from voice_chatbot.utils.logger import _get_global_logger
        logger = _get_global_logger()
        if logger is not None:
            logger.show_source = bool(value)


def apply_settings(manager, changes: Dict[str, Any]) -> Dict[str, Any]:
    """
    应用一批热设置变更

    Args:
        manager: DialogueManager 实例
        changes: {配置路径: 新值}

    Returns:
        {"ok": bool, "applied": [路径...], "errors": {路径: 错误信息}}
    """
    from voice_chatbot.utils.logger import log_info, log_warn

    applied = []
    errors: Dict[str, str] = {}

    for path, value in (changes or {}).items():
        setting = next((s for s in RUNTIME_SETTINGS if s["path"] == path), None)
        if setting is None:
            errors[path] = "未知或不可热修改的配置项"
            continue
        try:
            value = _coerce(setting, value)
            manager.config.set(path, value)     # 实时读取的参数立即生效
            _apply_special(manager, path, value)  # 固化字段的参数同步到对象
            applied.append(path)
            log_info("MANAGER", f"[热设置] {setting['label']}({path}) = {value}", indent=1)
        except (ValueError, TypeError) as e:
            errors[path] = str(e)
            log_warn("MANAGER", f"[热设置] 拒绝无效变更 {path}={value!r}: {e}", indent=1)

    # 持久化到 config.json（保持重启后生效）
    if applied:
        try:
            manager.config.save_to_file(str(manager.config.config_file))
        except Exception as e:
            errors["_persist"] = f"已生效但写入 config.json 失败: {e}"

    return {"ok": not errors, "applied": applied, "errors": errors}
