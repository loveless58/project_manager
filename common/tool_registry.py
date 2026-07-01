"""
Tool Registry - 工具注册与管理系统

设计原则：
- 每个工具必须有清晰的 name, description, parameters schema
- 模型通过 description 决定何时调用该工具
- 支持同步和异步工具
"""

from typing import Dict, Callable, Any, List
from dataclasses import dataclass, field
import inspect
import json


@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    parameters: Dict[str, Any] = field(default_factory=dict)
    required_params: List[str] = field(default_factory=list)
    
    def to_schema(self) -> Dict:
        """转换为模型可理解的工具描述"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": self.required_params,
            }
        }
    
    def __call__(self, **kwargs) -> Any:
        return self.func(**kwargs)


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
    
    def register(self, name: str, description: str, func: Callable, 
                 parameters: Dict[str, Any] = None, required_params: List[str] = None):
        """注册一个新工具"""
        # 自动推断参数
        if parameters is None:
            sig = inspect.signature(func)
            parameters = {}
            for param_name, param in sig.parameters.items():
                param_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation == int:
                        param_type = "integer"
                    elif param.annotation == float:
                        param_type = "number"
                    elif param.annotation == bool:
                        param_type = "boolean"
                    elif param.annotation == dict or str(param.annotation).startswith("dict"):
                        param_type = "object"
                    elif param.annotation == list or str(param.annotation).startswith("list"):
                        param_type = "array"
                
                parameters[param_name] = {
                    "type": param_type,
                    "description": f"Parameter: {param_name}",
                }
        
        if required_params is None:
            sig = inspect.signature(func)
            required_params = [
                p.name for p in sig.parameters.values()
                if p.default == inspect.Parameter.empty
            ]
        
        self._tools[name] = Tool(
            name=name,
            description=description,
            func=func,
            parameters=parameters,
            required_params=required_params,
        )
        print(f"🔧 Registered tool: {name}")
    
    def get(self, name: str) -> Tool:
        return self._tools.get(name)
    
    def list_tools(self) -> List[str]:
        return list(self._tools.keys())
    
    def to_schemas(self) -> List[Dict]:
        """输出所有工具的 schema，用于 LLM system prompt"""
        return [tool.to_schema() for tool in self._tools.values()]
    
    def to_prompt_text(self) -> str:
        """生成工具描述的文本格式，用于直接插入 prompt"""
        lines = ["## Available Tools\n"]
        for tool in self._tools.values():
            lines.append(f"### {tool.name}")
            lines.append(f"Description: {tool.description}")
            lines.append(f"Parameters: {json.dumps(tool.parameters, ensure_ascii=False)}")
            lines.append(f"Required: {', '.join(tool.required_params)}")
            lines.append("")
        return "\n".join(lines)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
    
    def __getitem__(self, name: str) -> Tool:
        return self._tools[name]


# 常用工具装饰器
def tool(name: str, description: str, parameters: Dict = None, required: List[str] = None):
    """工具装饰器"""
    def decorator(func):
        # 这里只是标记，实际注册需要 registry.register
        func._tool_meta = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "required": required,
        }
        return func
    return decorator
