"""
LLM Adapter - LLM 调用适配层

支持多种 LLM 后端：
1. Kimi Work 内置（通过 kimi_search_v2, kimi_fetch_v2 等工具）
2. 本地模型（通过 API 调用）
3. 模拟模式（用于测试和演示）

在 Kimi Work 环境中：
- 可以直接用 PythonRun 执行代码
- 可以通过外部 API 调用 Kimi 或其他模型
- 可以混合使用 Kimi 的搜索/获取工具 + 模型推理
"""

from typing import List, Dict, Any, Callable, Optional
import json
import os


class LLMResponse:
    """LLM 响应封装"""
    def __init__(self, text: str, tokens_used: int = 0, finish_reason: str = "stop"):
        self.text = text
        self.tokens_used = tokens_used
        self.finish_reason = finish_reason


class BaseLLMAdapter:
    """LLM 适配器基类"""
    
    def __init__(self, model_name: str = "default"):
        self.model_name = model_name
        self.total_tokens_used = 0
    
    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> LLMResponse:
        raise NotImplementedError
    
    def estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（Mock模式用保守估算）"""
        import re
        # Mock模式下估算偏保守，避免演示时过早触发预算
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_words = len(re.findall(r'[a-zA-Z]+', text))
        # 只计算新增内容的大致token
        return max(50, int(cn_chars * 0.5 + en_words * 0.3))


class MockLLMAdapter(BaseLLMAdapter):
    """
    模拟 LLM 适配器（用于测试和演示）
    
    不需要真实 API Key，可以模拟 ReAct 行为
    """
    
    def __init__(self, model_name: str = "mock"):
        super().__init__(model_name)
        self._step = 0
        self._mock_responses = []
    
    def set_mock_responses(self, responses: List[str]):
        """预设模拟响应列表"""
        self._mock_responses = responses
        self._step = 0
    
    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> LLMResponse:
        """模拟 LLM 调用"""
        if self._step < len(self._mock_responses):
            response = self._mock_responses[self._step]
            self._step += 1
        else:
            response = "Final Answer: 任务已完成（模拟模式结束）"
        
        tokens = self.estimate_tokens(str(messages))
        self.total_tokens_used += tokens
        return LLMResponse(text=response, tokens_used=tokens)


class APIAdapter(BaseLLMAdapter):
    """
    真实 API 适配器
    
    支持：
    - Moonshot Kimi API
    - OpenAI Compatible API
    - Anthropic Claude API
    - 其他 OpenAI 兼容接口
    """
    
    def __init__(
        self,
        model_name: str = "moonshot-v1-8k",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: str = "moonshot",  # moonshot, openai, anthropic
    ):
        super().__init__(model_name)
        self.provider = provider
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url
        
        if not self.api_key:
            print("⚠️ Warning: No API key provided. Set LLM_API_KEY env var.")
    
    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> LLMResponse:
        """调用真实 LLM API"""
        try:
            import requests
        except ImportError:
            return LLMResponse(
                text="[ERROR] requests library not installed. Run: pip install requests",
                tokens_used=0
            )
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        if self.provider == "moonshot":
            url = self.base_url or "https://api.moonshot.cn/v1/chat/completions"
        elif self.provider == "openai":
            if self.base_url:
                # base_url is conventionally the API root (e.g. http://host/v1);
                # append the chat-completions suffix unless it's already there.
                base = self.base_url.rstrip("/")
                url = base if base.endswith("/chat/completions") else base + "/chat/completions"
            else:
                url = "https://api.openai.com/v1/chat/completions"
        elif self.provider == "anthropic":
            if self.base_url:
                base = self.base_url.rstrip("/")
                url = base if base.endswith("/messages") else base + "/v1/messages"
            else:
                url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
            del headers["Authorization"]
        else:
            url = self.base_url
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.3,
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            data = resp.json()
            
            if self.provider == "anthropic":
                text = data.get("content", [{}])[0].get("text", "")
                tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)
            else:
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                tokens = data.get("usage", {}).get("total_tokens", 0)
            
            self.total_tokens_used += tokens
            return LLMResponse(text=text, tokens_used=tokens)
            
        except Exception as e:
            return LLMResponse(
                text=f"[API ERROR] {str(e)}",
                tokens_used=0,
            )


class KimiWorkAdapter(BaseLLMAdapter):
    """
    Kimi Work 环境专用适配器
    
    在 Kimi Work 环境中，可以直接利用：
    1. PythonRun 执行代码
    2. kimi_search_v2 搜索工具
    3. kimi_fetch_v2 网页抓取
    4. 文件读写能力
    
    使用方式：
    - 当 Agent 需要推理时，直接生成 Python 代码并执行
    - 通过文件系统保存中间状态
    - 通过工具调用获取外部信息
    """
    
    def __init__(self, model_name: str = "kimi-work-local"):
        super().__init__(model_name)
    
    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> LLMResponse:
        """
        Kimi Work 环境下的 LLM 调用
        
        实际实现：将 messages 转成 prompt，然后执行
        在真实环境中，这可以通过调用 Kimi 的 API 或本地模型完成
        """
        # 构建完整 prompt
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"[{role.upper()}]\n{content}")
        
        full_prompt = "\n\n".join(prompt_parts)
        
        # 估算 token
        tokens = self.estimate_tokens(full_prompt)
        self.total_tokens_used += tokens
        
        # 在 Kimi Work 环境中，这里实际会触发一次模型推理
        # 当前框架中，我们返回一个占位符，由调用方决定如何执行
        return LLMResponse(
            text="[KIMI_WORK] 请通过实际环境执行推理。\nPrompt 已构建完成。",
            tokens_used=tokens,
        )


def build_react_prompt(goal: str, tools_text: str, memory_text: str = "") -> str:
    """
    构建标准的 ReAct System Prompt
    
    这是 Loop 框架的核心 prompt 模板
    """
    prompt = f"""You are an autonomous agent operating in a ReAct loop. Your goal is to complete the following task through a cycle of Thought, Action, and Observation.

## Task
{goal}

{tools_text}

## Rules
1. You MUST use the format:
   Thought: <your reasoning>
   Action: <tool_name>
   Action Input: <json_params>

2. If you have completed the task, use:
   Final Answer: <your conclusion>

3. When a tool fails repeatedly, try a different approach or report the failure.

4. Be concise. Focus on completing the task efficiently.

{memory_text}

Now begin."""
    return prompt
