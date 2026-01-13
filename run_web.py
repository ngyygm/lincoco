#!/usr/bin/env python3
"""
使用 Web 麦克风的语音对话系统启动脚本

这个脚本会启动语音对话系统，并配置使用 Web 麦克风（通过浏览器远程采集音频）。
"""

import sys
import os
import json

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from voice_chatbot.dialogue.manager import DialogueManager
from voice_chatbot.utils.config import Config
from voice_chatbot.utils import log_info, log_error, log_warn, init_logger, update_logger_filter, update_logger_category_colors


def main():
    """主函数"""
    # 加载配置（用于初始化日志系统）
    config = Config()

    # 初始化日志系统（从配置读取过滤参数和类别颜色）
    log_file_enabled = config.get("logging.file_enabled", False)
    log_dir = config.get("logging.log_dir", "cache/logs")
    init_logger(
        log_file_enabled=log_file_enabled,
        log_dir=log_dir,
        config=config
    )

    # 更新 Logger 的过滤配置和类别颜色
    update_logger_filter(config)
    update_logger_category_colors(config)

    log_info("MAIN", "===== Web 麦克风语音对话系统 =====", indent=0)

    # 重新加载配置（因为之前已经加载过，这里为了保持一致性）
    log_info("MAIN", "加载配置...", indent=0)

    # 启用 Web 麦克风模式（通过设置 system.websocket.enabled）
    config.set("system.websocket.enabled", True)

    # 获取 Web 麦克风配置（从 system.websocket 获取）
    ws_config = config.get("system.websocket", {})
    web_mic_host = ws_config.get("host", "0.0.0.0")
    web_mic_port = ws_config.get("port", 8766)

    # 显示配置信息
    log_info("MAIN", "Web 麦克风配置:", indent=1)
    log_info("MAIN", f"  - 监听地址: {web_mic_host}:{web_mic_port}", indent=2)

    # 提示用户打开浏览器
    log_info("MAIN", "使用说明:", indent=0)
    log_info("MAIN", f"1. 在浏览器中打开: {PROJECT_ROOT}/web_microphone.html", indent=1)
    log_info("MAIN", f"2. 点击'连接服务器'按钮，连接到: ws://{web_mic_host}:{web_mic_port}", indent=1)
    log_info("MAIN", f"3. 点击'开始录音'按钮开始采集音频", indent=1)
    log_info("MAIN", f"4. 对着麦克风说话，系统会自动进行语音识别和对话", indent=1)

    # 初始化系统
    log_info("MAIN", "初始化系统...", indent=0)
    try:
        manager = DialogueManager(config)
        manager.initialize_modules()
        log_info("MAIN", "系统初始化完成", indent=0)

    except Exception as e:
        log_error("MAIN", f"初始化系统失败: {e}", indent=0)
        import traceback
        log_error("MAIN", traceback.format_exc(), indent=1)
        return 1

    # 启动系统
    log_info("MAIN", "启动系统...", indent=0)
    try:
        manager.start()
        log_info("MAIN", "系统已启动", indent=0)

    except Exception as e:
        log_error("MAIN", f"启动系统失败: {e}", indent=0)
        import traceback
        log_error("MAIN", traceback.format_exc(), indent=1)
        return 1

    # 等待客户端连接和音频传输
    log_info("MAIN", "等待客户端连接和音频传输...", indent=0)
    log_info("MAIN", "（声纹注册和欢迎语音将在连接成功后执行）", indent=1)

    try:
        import time

        client_connected = False
        audio_transmitted = False
        greeting_played = False
        speaker_verification_done = False
        initialization_done = False  # 初始化任务完成标志

        last_stats_time = time.time()
        stats_interval = 30  # 每 30 秒打印一次统计信息

        # 初始化循环：等待客户端连接和音频传输，执行初始化任务
        while not initialization_done:
            time.sleep(1)

            # 检查客户端连接状态
            if manager.microphone_type == "web" and hasattr(manager, 'microphone'):
                stats = manager.microphone.get_statistics()
                active_clients = stats.get('active_clients', 0)
                total_samples = stats.get('total_samples', 0)

                # 检查是否有客户端连接
                if not client_connected and active_clients > 0:
                    client_connected = True
                    log_info("MAIN", "✓ 检测到客户端连接", indent=0)

                # 检查是否有音频传输
                if not audio_transmitted and total_samples > 0:
                    audio_transmitted = True
                    log_info("MAIN", f"✓ 检测到音频传输 (已接收 {total_samples} 样本)", indent=0)

                # 客户端连接和音频传输都就绪后，执行初始化任务
                if client_connected and audio_transmitted:
                    log_info("MAIN", "=== 系统就绪，开始执行初始化任务 ===", indent=0)

                    # 1. 执行声纹注册（如果启用且需要）
                    # 检查并执行声纹注册（如果启用且需要）
                    if manager.asr and hasattr(manager.asr, 'speaker_verification') and manager.asr.speaker_verification:
                        log_info("MAIN", "检查声纹注册状态...", indent=0)
                        try:
                            # 使用已加载的ASR模型、TTS引擎和音频播放器
                            manager.asr.speaker_verification.check_and_enroll_if_needed(
                                asr_model=manager.asr.asr_model if hasattr(manager.asr, 'asr_model') else None,
                                tts_engine=manager.tts_engine,
                                audio_player=manager.audio_player
                            )
                            speaker_verification_done = True
                            log_info("MAIN", "声纹注册检查完成", indent=0)
                        except Exception as e:
                            log_error("MAIN", f"声纹注册检查失败: {e}", indent=1)
                            import traceback
                            log_error("MAIN", f"异常堆栈: {traceback.format_exc()}", indent=1)
                    else:
                        log_info("MAIN", "声纹功能未启用或ASR不支持声纹，跳过注册", indent=1)

                    # 2. 播放欢迎语音（如果启用）
                    greeting_config = config.get("system.greeting", {})
                    greeting_enabled = greeting_config.get("enabled", False)
                    if greeting_enabled:
                        greeting_text = greeting_config.get("text", "")
                        if greeting_text:
                            try:
                                # 使用manager中的tts_engine生成语音
                                if manager.tts_engine:
                                    log_info("MAIN", f"生成欢迎语音: {greeting_text}", indent=0)
                                    audio_segment = manager.tts_engine.generate(greeting_text)
                                    log_info("MAIN", f"播放欢迎语音: {greeting_text}", indent=0)
                                    # 使用manager中的audio_player播放语音
                                    if audio_segment is not None:
                                        # 获取audio_player实例
                                        audio_player = manager.audio_player
                                        # 播放音频（使用task_id=None跳过任务取消检查）
                                        audio_player.play(audio_segment, current_sentence_text=greeting_text, task_id=None)
                                        greeting_played = True
                                        log_info("MAIN", "欢迎语音播放完成", indent=0)
                                    else:
                                        log_error("MAIN", "欢迎语生成失败", indent=1)
                                else:
                                    log_warn("MAIN", "TTS引擎未初始化，跳过欢迎语", indent=1)
                            except Exception as e:
                                log_error("MAIN", f"欢迎语播放出错: {e}", indent=1)
                                import traceback
                                log_error("MAIN", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
                        else:
                            log_info("MAIN", "欢迎语未配置，跳过", indent=1)
                    else:
                        log_info("MAIN", "欢迎语未启用，跳过", indent=1)

                    log_info("MAIN", "=== 初始化任务完成 ===", indent=0)
                    
                    # 标记初始化任务完成，跳出循环
                    initialization_done = True
                    break

            # 定期打印统计信息
            current_time = time.time()
            if current_time - last_stats_time >= stats_interval:
                if manager.microphone_type == "web" and hasattr(manager, 'microphone'):
                    stats = manager.microphone.get_statistics()
                    log_info("MAIN", "===== 统计信息 =====", indent=0)
                    log_info("MAIN", f"Web 麦克风服务端:", indent=1)
                    log_info("MAIN", f"  - 活跃客户端: {stats['active_clients']}", indent=2)
                    log_info("MAIN", f"  - 总样本数: {stats['total_samples']:,}", indent=2)
                    log_info("MAIN", f"  - 总时长: {stats['total_duration']:.2f}秒", indent=2)
                    log_info("MAIN", f"  - 客户端已连接: {client_connected}", indent=2)
                    log_info("MAIN", f"  - 音频已传输: {audio_transmitted}", indent=2)
                    log_info("MAIN", f"  - 声纹注册: {'已完成' if speaker_verification_done else '未执行'}", indent=2)
                    log_info("MAIN", f"  - 欢迎语音: {'已播放' if greeting_played else '未播放'}", indent=2)

                last_stats_time = current_time
        
        # 主运行循环（和main.py一样，简单保持系统运行）
        log_info("MAIN", "====== 进入主运行循环 =====", indent=0)
        log_info("MAIN", "系统运行中，按 Ctrl+C 退出", indent=0)
        
        try:
            # 主循环：保持系统运行
            # 注意：KeyboardInterrupt会被信号处理器捕获
            # 主循环只需要简单地sleep即可（和main.py一样）
            while manager.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            log_info("MAIN", "收到键盘中断信号", indent=0)

    except Exception as e:
        log_error("MAIN", f"系统失败: {e}", indent=0)
        import traceback
        log_error("MAIN", traceback.format_exc(), indent=1)
        return 1

    # 停止系统
    log_info("MAIN", "停止系统...", indent=0)
    try:
        manager.stop()
        log_info("MAIN", "系统已停止", indent=0)
    except Exception as e:
        log_error("MAIN", f"停止系统时出错: {e}", indent=0)
        import traceback
        log_error("MAIN", traceback.format_exc(), indent=1)
        return 1

    log_info("MAIN", "===== 系统已退出 =====", indent=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
