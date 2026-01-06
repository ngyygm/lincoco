"""
标点恢复工具模块

使用FunASR的标点恢复模型对ASR识别结果进行标点符号恢复
"""

from typing import Optional

try:
    from funasr import AutoModel
    HAS_FUNASR = True
except ImportError:
    HAS_FUNASR = False
    AutoModel = None


class PunctuationRestorer:
    """标点恢复器"""
    
    def __init__(self, model_name: str = "ct-punc"):
        """
        初始化标点恢复器
        
        Args:
            model_name: FunASR标点恢复模型名称，默认为"ct-punc"
        """
        if not HAS_FUNASR:
            raise ImportError("funasr库未安装，请安装：pip install funasr")
        
        self.model_name = model_name
        self.model: Optional[AutoModel] = None
        self._initialized = False
    
    def initialize(self):
        """初始化标点恢复模型（延迟加载）"""
        if self._initialized:
            return
        
        try:
            self.model = AutoModel(model=self.model_name, disable_pbar=True)
            self._initialized = True
        except Exception as e:
            raise RuntimeError(f"初始化标点恢复模型失败: {e}")
    
    def restore(self, text: str) -> str:
        """
        对文本进行标点恢复
        
        Args:
            text: 需要恢复标点的文本（通常是无标点的ASR识别结果）
            
        Returns:
            恢复标点后的文本
        """
        if not text or not text.strip():
            return text
        
        # 如果模型未初始化，先初始化
        if not self._initialized:
            self.initialize()
        
        try:
            # 使用FunASR的标点恢复模型
            result = self.model.generate(input=text)
            
            # 处理返回结果
            # FunASR的generate方法通常返回列表，每个元素是字典包含'text'键
            if result:
                if isinstance(result, list) and len(result) > 0:
                    # 如果是列表，取第一个元素
                    first_item = result[0]
                    if isinstance(first_item, dict):
                        # 字典格式，提取text字段
                        restored_text = first_item.get('text', text)
                        return restored_text if restored_text else text
                    elif isinstance(first_item, str):
                        # 字符串格式，直接返回
                        return first_item
                    else:
                        # 其他格式，转换为字符串
                        return str(first_item)
                elif isinstance(result, str):
                    # 直接返回字符串
                    return result
                else:
                    # 其他格式，尝试转换为字符串
                    return str(result) if result else text
            else:
                return text
        except Exception as e:
            # 如果标点恢复失败，返回原始文本
            print(f"警告：标点恢复失败: {e}，返回原始文本")
            return text
    
    def cleanup(self):
        """清理资源"""
        self.model = None
        self._initialized = False

