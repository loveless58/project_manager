"""
State Manager - 状态管理三种方案

支持：
1. full - 全量历史（默认，短任务≤5轮）
2. sliding_window - 滑动窗口（长任务，早期信息不重要）
3. summary - 摘要压缩（长任务，早期信息有价值）
"""

from typing import List, Dict, Any, Optional, Callable
import json


class StateManager:
    """状态管理器"""
    
    def __init__(self, mode: str = "full", window_size: int = 3):
        self.mode = mode
        self.window_size = window_size
        self._summary_cache: Optional[str] = None
    
    def trim(self, messages: List[Dict]) -> List[Dict]:
        """根据模式裁剪消息历史"""
        if self.mode == "full":
            return self._trim_full(messages)
        elif self.mode == "sliding_window":
            return self._trim_sliding(messages)
        elif self.mode == "summary":
            return self._trim_summary(messages)
        else:
            raise ValueError(f"Unknown state mode: {self.mode}")
    
    def _trim_full(self, messages: List[Dict]) -> List[Dict]:
        """全量保留"""
        return messages
    
    def _trim_sliding(self, messages: List[Dict]) -> List[Dict]:
        """
        滑动窗口：保留最初用户请求 + 最近 N 轮交互
        
        关键规则：必须按「轮」裁剪，不能把 tool_use 和 tool_result 拆散
        
        消息结构假设：
        - 前2条：system + 初始 user
        - 之后每轮3条：assistant(thought/action) + user(observation) + assistant(思考)
        """
        if len(messages) <= 2 + self.window_size * 3:
            return messages
        
        # 保留头部（系统提示 + 初始请求）
        head = messages[:2]
        
        # 从尾部取完整轮次
        # 每轮包含：assistant + observation(user) + ...
        # 这里简化为：保留最后 window_size * 3 条
        # 实际应用时建议按完整的 thought-action-observation 轮次裁剪
        tail = messages[-(self.window_size * 3):]
        
        trimmed = head + tail
        print(f"📐 SlidingWindow: {len(messages)} → {len(trimmed)} messages (head={len(head)}, tail={len(tail)})")
        return trimmed
    
    def _trim_summary(self, messages: List[Dict]) -> List[Dict]:
        """
        摘要压缩：用额外 API 调用把早期历史压缩成摘要
        
        注意：此方法需要外部 LLM 调用，这里提供框架，实际由 loop 调用方注入
        """
        # 如果消息不多，直接保留
        if len(messages) <= 10:
            return messages
        
        # 保留前2条（系统+初始）+ 最近6条 + 摘要替代中间部分
        head = messages[:2]
        tail = messages[-6:]
        
        # 中间部分被摘要替代（需要外部生成摘要）
        middle_messages = messages[2:-6]
        
        # 如果有缓存的摘要，直接复用
        if self._summary_cache:
            summary_msg = {
                "role": "user",
                "content": f"[SUMMARY OF EARLIER CONVERSATION]\n{self._summary_cache}"
            }
            result = head + [summary_msg] + tail
        else:
            # 没有摘要时，先滑动窗口处理，等待后续生成摘要
            result = head + tail
        
        print(f"📐 SummaryMode: {len(messages)} → {len(result)} messages (summary pending)")
        return result
    
    def generate_summary(self, messages: List[Dict], llm_summarizer: Callable[[str], str]) -> str:
        """
        生成摘要。需要外部提供 LLM 调用函数。
        
        Args:
            messages: 需要被摘要的消息列表
            llm_summarizer: fn(text) -> summary_text
        """
        # 提取需要摘要的消息内容
        texts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            texts.append(f"{role}: {content[:200]}")
        
        full_text = "\n".join(texts)
        prompt = f"""请将以下对话历史压缩成3句话以内的摘要，保留关键结论和上下文：

{full_text}

摘要（3句话以内）："""
        
        summary = llm_summarizer(prompt)
        self._summary_cache = summary
        return summary


class MemoryStore:
    """
    持久化记忆存储（基于文件系统）
    
    Kimi Work / Hermes 环境建议：
    - 用文件系统（markdown/json）保存状态
    - 不要用内存缓存，因为每次重启会丢失
    """
    
    def __init__(self, base_dir: str = "./state"):
        self.base_dir = base_dir
        import os
        os.makedirs(base_dir, exist_ok=True)
    
    def save(self, key: str, data: Any):
        """保存状态"""
        import os
        path = os.path.join(self.base_dir, f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def load(self, key: str) -> Optional[Any]:
        """读取状态"""
        import os
        path = os.path.join(self.base_dir, f"{key}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def append(self, key: str, item: Dict):
        """追加记录（如 TODO 列表）"""
        data = self.load(key) or []
        if isinstance(data, list):
            data.append(item)
        else:
            data = [item]
        self.save(key, data)
    
    def list_keys(self) -> List[str]:
        """列出所有存储的 key"""
        import os
        files = os.listdir(self.base_dir)
        return [f.replace(".json", "") for f in files if f.endswith(".json")]
