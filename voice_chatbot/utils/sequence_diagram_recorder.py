"""
序列图记录器模块

用于跟踪关键流程中的事件序列，生成mermaid sequenceDiagram格式的图形化流程记录
"""

import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class SequenceMessage:
    """序列图中的消息"""
    from_participant: str
    to_participant: str
    message: str
    timestamp: float
    event_type: Optional[str] = None  # 事件类型（用于格式化）


@dataclass
class SequenceNote:
    """序列图中的注释"""
    participant: str  # "over" 或 "right of" 等位置
    note_text: str
    timestamp: float


class SequenceDiagramRecorder:
    """
    序列图记录器
    
    用于跟踪关键流程中的事件序列，生成mermaid sequenceDiagram格式的文本
    """
    
    # 参与者名称映射（简化显示）
    PARTICIPANT_NAMES = {
        "VAD": "VAD",
        "ASR": "ASR",
        "Manager": "DialogueManager",
        "LLM": "LLM",
        "TTS": "TTS",
        "Player": "Player",
    }
    
    # 事件类型到参与者的映射
    EVENT_TO_PARTICIPANT = {
        "VAD_SPEECH_START": "VAD",
        "VAD_SPEECH_END": "VAD",
        "ASR_RECOGNITION_START": "ASR",
        "ASR_PARTIAL_RESULT": "ASR",
        "ASR_STABLE_RESULT": "ASR",
        "ASR_FINAL_RESULT": "ASR",
        "ASR_RECOGNITION_END": "ASR",
        "LLM_GENERATION_START": "LLM",
        "LLM_REQUEST_SENT": "LLM",
        "LLM_STREAM_START": "LLM",
        "LLM_SENTENCE_GENERATED": "LLM",
        "LLM_FIRST_SENTENCE": "LLM",
        "LLM_GENERATION_COMPLETE": "LLM",
        "TTS_GENERATION_START": "TTS",
        "TTS_CACHE_HIT": "TTS",
        "TTS_FIRST_AUDIO_READY": "TTS",
        "TTS_GENERATION_COMPLETE": "TTS",
        "AUDIO_PLAY_START": "Player",
        "AUDIO_PLAY_END": "Player",
        "AUDIO_PAUSED": "Player",
        "AUDIO_RESUMED": "Player",
        "TASK_CREATED": "Manager",
        "TASK_CANCELLED": "Manager",
        "USER_INTERRUPT": "Manager",
    }
    
    def __init__(self, flow_name: str = "关键流程"):
        """
        初始化序列图记录器
        
        Args:
            flow_name: 流程名称（用于标识）
        """
        self.flow_name = flow_name
        self.start_time: Optional[float] = None
        self.participants: Set[str] = set()
        self.messages: List[SequenceMessage] = []
        self.notes: List[SequenceNote] = []
        self.task_id: Optional[int] = None  # 当前流程的任务ID
        
    def reset(self, flow_name: Optional[str] = None):
        """
        重置记录器（用于新流程）
        
        Args:
            flow_name: 新的流程名称（如果提供）
        """
        if flow_name:
            self.flow_name = flow_name
        self.start_time = None
        self.participants.clear()
        self.messages.clear()
        self.notes.clear()
        self.task_id = None
    
    def start_flow(self, task_id: Optional[int] = None):
        """
        开始记录流程
        
        Args:
            task_id: 任务ID（可选）
        """
        self.start_time = time.time()
        self.task_id = task_id
    
    def add_participant(self, participant: str):
        """
        添加参与者
        
        Args:
            participant: 参与者名称
        """
        self.participants.add(participant)
    
    def add_message(self, 
                   from_participant: str,
                   to_participant: str,
                   message: str,
                   event_type: Optional[str] = None,
                   timestamp: Optional[float] = None):
        """
        记录消息传递
        
        Args:
            from_participant: 发送者
            to_participant: 接收者
            message: 消息内容
            event_type: 事件类型（可选）
            timestamp: 时间戳（如果为None则使用当前时间）
        """
        if timestamp is None:
            timestamp = time.time()
        
        self.participants.add(from_participant)
        self.participants.add(to_participant)
        
        self.messages.append(SequenceMessage(
            from_participant=from_participant,
            to_participant=to_participant,
            message=message,
            timestamp=timestamp,
            event_type=event_type
        ))
    
    def add_note(self, 
                 participant: str,
                 note_text: str,
                 timestamp: Optional[float] = None):
        """
        添加注释
        
        Args:
            participant: 注释位置（如 "Manager" 或 "over Manager"）
            note_text: 注释文本
            timestamp: 时间戳（如果为None则使用当前时间）
        """
        if timestamp is None:
            timestamp = time.time()
        
        self.notes.append(SequenceNote(
            participant=participant,
            note_text=note_text,
            timestamp=timestamp
        ))
    
    def add_event(self, 
                  event_type: str,
                  from_participant: Optional[str] = None,
                  to_participant: Optional[str] = None,
                  message: Optional[str] = None,
                  timestamp: Optional[float] = None,
                  data: Optional[Dict] = None):
        """
        根据事件类型自动添加消息
        
        Args:
            event_type: 事件类型（如 "VAD_SPEECH_END"）
            from_participant: 发送者（如果为None则根据事件类型推断）
            to_participant: 接收者（如果为None则默认为"Manager"）
            message: 消息内容（如果为None则使用事件类型）
            timestamp: 时间戳
            data: 事件数据（用于格式化消息）
        """
        # 推断参与者
        if from_participant is None:
            from_participant = self.EVENT_TO_PARTICIPANT.get(event_type, "Manager")
        
        if to_participant is None:
            to_participant = "Manager"
        
        # 格式化消息
        if message is None:
            message = self._format_event_message(event_type, data)
        
        # 添加消息
        self.add_message(
            from_participant=from_participant,
            to_participant=to_participant,
            message=message,
            event_type=event_type,
            timestamp=timestamp
        )
    
    def _format_event_message(self, event_type: str, data: Optional[Dict]) -> str:
        """
        格式化事件消息
        
        Args:
            event_type: 事件类型
            data: 事件数据
            
        Returns:
            格式化的消息字符串
        """
        # 获取相对时间戳
        relative_time = ""
        if self.start_time and data and "timestamp" in data:
            elapsed = data["timestamp"] - self.start_time
            relative_time = f" ({elapsed:.3f})"
        elif self.start_time:
            elapsed = time.time() - self.start_time
            relative_time = f" ({elapsed:.3f})"
        
        # 根据事件类型格式化
        if event_type == "VAD_SPEECH_START":
            return f"VAD_SPEECH_START{relative_time}"
        elif event_type == "VAD_SPEECH_END":
            return f"VAD_SPEECH_END{relative_time}"
        elif event_type == "ASR_STABLE_RESULT":
            text = data.get("text", "") if data else ""
            if text:
                text_preview = text[:20] + "..." if len(text) > 20 else text
                return f"ASR_STABLE_RESULT: {text_preview}{relative_time}"
            return f"ASR_STABLE_RESULT{relative_time}"
        elif event_type == "ASR_FINAL_RESULT":
            text = data.get("text", "") if data else ""
            if text:
                text_preview = text[:20] + "..." if len(text) > 20 else text
                return f"ASR_FINAL_RESULT: {text_preview}{relative_time}"
            return f"ASR_FINAL_RESULT{relative_time}"
        elif event_type == "LLM_GENERATION_START":
            return f"LLM_GENERATION_START{relative_time}"
        elif event_type == "TTS_GENERATION_START":
            return f"TTS_GENERATION_START{relative_time}"
        elif event_type == "AUDIO_PLAY_START":
            return f"AUDIO_PLAY_START{relative_time}"
        elif event_type == "AUDIO_PLAY_END":
            return f"AUDIO_PLAY_END{relative_time}"
        elif event_type == "USER_INTERRUPT":
            return f"USER_INTERRUPT{relative_time}"
        elif event_type == "TASK_CANCELLED":
            return f"TASK_CANCELLED{relative_time}"
        else:
            # 默认格式
            return f"{event_type}{relative_time}"
    
    def generate_diagram(self) -> str:
        """
        生成mermaid sequenceDiagram格式的文本
        
        Returns:
            sequenceDiagram文本
        """
        if not self.messages:
            return ""
        
        lines = ["sequenceDiagram"]
        
        # 添加参与者（按出现顺序）
        participant_list = sorted(list(self.participants))
        for participant in participant_list:
            display_name = self.PARTICIPANT_NAMES.get(participant, participant)
            if display_name != participant:
                lines.append(f'    participant {participant} as {display_name}')
            else:
                lines.append(f'    participant {participant}')
        
        lines.append("")  # 空行分隔
        
        # 合并消息和注释，按时间戳排序
        all_items = []
        for msg in self.messages:
            all_items.append(("message", msg.timestamp, msg))
        for note in self.notes:
            all_items.append(("note", note.timestamp, note))
        
        all_items.sort(key=lambda x: x[1])  # 按时间戳排序
        
        # 生成消息和注释
        for item_type, _, item in all_items:
            if item_type == "message":
                msg = item
                # 转义特殊字符
                safe_message = self._escape_mermaid_text(msg.message)
                lines.append(f'    {msg.from_participant}->>{msg.to_participant}: {safe_message}')
            elif item_type == "note":
                note = item
                safe_note = self._escape_mermaid_text(note.note_text)
                if note.participant.startswith("over "):
                    lines.append(f'    Note over {note.participant[5:]}: {safe_note}')
                elif note.participant.startswith("right of "):
                    lines.append(f'    Note right of {note.participant[9:]}: {safe_note}')
                else:
                    lines.append(f'    Note over {note.participant}: {safe_note}')
        
        return "\n".join(lines)
    
    def _escape_mermaid_text(self, text: str) -> str:
        """
        转义mermaid文本中的特殊字符
        
        Args:
            text: 原始文本
            
        Returns:
            转义后的文本
        """
        # 转义引号、冒号等特殊字符
        # 注意：mermaid中某些字符可能需要转义，但通常直接使用也可以
        # 这里主要处理可能导致语法错误的字符
        text = text.replace('"', '\\"')
        # 如果包含换行符，替换为空格
        text = text.replace('\n', ' ')
        text = text.replace('\r', ' ')
        return text
    
    def is_empty(self) -> bool:
        """检查是否为空（没有消息）"""
        return len(self.messages) == 0
    
    def get_flow_duration(self) -> Optional[float]:
        """
        获取流程持续时间（秒）
        
        Returns:
            持续时间，如果流程未开始则返回None
        """
        if self.start_time is None:
            return None
        return time.time() - self.start_time





