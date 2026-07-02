import json
import ast
import time
import uuid
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime
import traceback


@dataclass
class LoopRound:
    """单轮循环记录"""
    round_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    intent: str = ""
    thought: str = ""
    action: str = ""
    action_input: Dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    observation_evaluation: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # running, success, failed, blocked
    token_cost: int = 0


@dataclass
class LoopTrace:
    """完整循环轨迹"""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent_name: str = ""
    goal: str = ""
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    end_time: Optional[str] = None
    rounds: List[LoopRound] = field(default_factory=list)
    total_token_cost: int = 0
    final_result: str = ""
    status: str = "running"  # running, completed, failed, timeout, budget_exceeded
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def print_summary(self):
        print("\n" + "="*60)
        print(f"🔄 Loop Trace Summary [{self.trace_id}]")
        print("="*60)
        print(f"Agent: {self.agent_name}")
        print(f"Goal: {self.goal}")
        print(f"Status: {self.status}")
        print(f"Rounds: {len(self.rounds)}")
        print(f"Total Token Cost: {self.total_token_cost}")
        print(f"Start: {self.start_time}")
        if self.end_time:
            print(f"End: {self.end_time}")
        print("-"*60)
        for i, r in enumerate(self.rounds, 1):
            print(f"  Round {i}: [{r.status}] {r.action}")
            if r.observation:
                obs = r.observation[:100] + "..." if len(r.observation) > 100 else r.observation
                print(f"    → {obs}")
        print("="*60)


class LoopEngine:
    """
    Loop 核心引擎
    
    功能：
    - ReAct 循环执行
    - 终止条件检测（最大轮数、重复调用、token预算、目标达成）
    - 重复调用检测（dedup）
    - 状态管理（全量/滑动窗口/摘要压缩）
    - 错误恢复（重试 + 结构化错误信息）
    - Token 预算控制
    - 可观测性（Trace记录）
    """
    
    def __init__(
        self,
        agent_name: str = "default_agent",
        max_rounds: int = 10,
        dedup_threshold: int = 2,
        token_budget: Optional[int] = None,  # 总token预算
        state_mode: str = "full",  # full, sliding_window, summary
        sliding_window_size: int = 3,
        retry_max: int = 2,
        trace_dir: str = "./logs",
    ):
        self.agent_name = agent_name
        self.max_rounds = max_rounds
        self.dedup_threshold = dedup_threshold
        self.token_budget = token_budget
        self.state_mode = state_mode
        self.sliding_window_size = sliding_window_size
        self.retry_max = retry_max
        self.trace_dir = trace_dir
        
        # 运行时状态
        self.call_history: Dict[str, int] = {}  # 用于重复调用检测
        self.current_trace: Optional[LoopTrace] = None
        self.total_tokens_used: int = 0
        
    def _check_dedup(self, action_name: str, action_input: Dict) -> bool:
        """重复调用检测。返回 True 表示需要拦截"""
        call_signature = f"{action_name}:{json.dumps(action_input, sort_keys=True)}"
        count = self.call_history.get(call_signature, 0) + 1
        self.call_history[call_signature] = count
        return count > self.dedup_threshold
    
    def _check_budget(self, token_cost: int) -> bool:
        """检查token预算是否超支"""
        self.total_tokens_used += token_cost
        if self.token_budget is not None and self.total_tokens_used > self.token_budget:
            return False
        return True
    
    def _manage_state(self, messages: List[Dict]) -> List[Dict]:
        """状态管理：按模式裁剪历史"""
        if self.state_mode == "full":
            return messages
        
        elif self.state_mode == "sliding_window":
            # 保留：系统提示 + 初始用户请求 + 最近 N 轮(tool_use+tool_result+assistant)
            # 注意：必须按「轮」裁剪，不能把 tool_use 和 tool_result 拆散
            # 这里简化为保留前2条（系统+初始）+ 最近 3*3 = 9条
            if len(messages) <= 2 + self.sliding_window_size * 3:
                return messages
            head = messages[:2]  # 系统提示 + 初始请求
            # 找到完整的轮次（从后往前，每轮包含 assistant + tool_use + tool_result）
            tail = messages[-(self.sliding_window_size * 3):]
            return head + tail
        
        elif self.state_mode == "summary":
            # 摘要压缩：需要额外实现 summarize_history()
            # 这里先预留接口，实际运行时由 agent 自己实现或外部调用
            return messages
        
        return messages
    
    def _execute_with_retry(self, action_name: str, action_input: Dict, 
                           tools: Dict[str, Callable]) -> tuple[str, bool]:
        """
        带重试的工具执行 + 错误恢复
        
        retry_max 表示失败后额外重试的最大次数。
        总尝试次数 = 1（初始）+ retry_max（重试）
        
        返回: (observation, success)
        """
        tool_func = tools.get(action_name)
        if not tool_func:
            return f"[ERROR] Tool '{action_name}' not found.", False
        
        last_error = None
        max_attempts = self.retry_max + 1  # 初始 1 次 + retry_max 次重试
        for attempt in range(max_attempts):
            try:
                result = tool_func(**action_input)
                return str(result), True
            except Exception as e:
                last_error = e
                if attempt < self.retry_max:
                    time.sleep(0.5)  # 短暂重试间隔
        
        # 所有重试失败，返回结构化错误信息
        structured_error = (
            f"[TOOL FAILED after {self.retry_max} retries ({max_attempts} attempts total)]\n"
            f"Tool: {action_name}\n"
            f"Input: {action_input}\n"
            f"Error: {str(last_error)}\n"
            f"Trace: {traceback.format_exc()[:500]}\n"
            f"[SUGGESTION] Please try a different approach or report this failure."
        )
        return structured_error, False

    def _evaluate_observation(self, observation: str, success: bool) -> Dict[str, Any]:
        """Classify a raw tool observation into a planner-facing control signal."""
        parsed = self._parse_observation_payload(observation)
        status = "success" if success else "failed"
        error_code = ""
        retryable = not success
        needs_confirmation = False
        next_actions: List[str] = []
        summary = observation[:300]

        if isinstance(parsed, dict):
            raw_status = str(parsed.get("status") or "").lower()
            ok = parsed.get("ok")
            if raw_status in {"success", "partial", "blocked", "failed", "needs_confirmation"}:
                status = raw_status
            elif ok is False:
                status = "failed"

            blocked_reason = parsed.get("blocked_reason")
            error_text = str(parsed.get("error") or "")
            if blocked_reason:
                error_code = str(blocked_reason)
            elif error_text:
                error_code = error_text.split(":", 1)[0].strip()

            needs_confirmation = status == "needs_confirmation" or bool(parsed.get("pending_confirmation"))
            if needs_confirmation:
                status = "needs_confirmation"
            if status == "blocked":
                retryable = False
            elif status == "needs_confirmation":
                retryable = False
            elif status == "partial":
                retryable = False
            elif status == "failed":
                retryable = bool(parsed.get("retryable", True))
            else:
                retryable = False

            next_steps = parsed.get("next_steps") or parsed.get("next_actions") or []
            if isinstance(next_steps, list):
                next_actions = [str(item) for item in next_steps]
            elif next_steps:
                next_actions = [str(next_steps)]
            summary = self._summarize_payload(parsed, fallback=summary)
        else:
            lowered = observation.lower()
            if observation.startswith("[DEDUP]"):
                status = "blocked"
                error_code = "dedup_threshold"
                retryable = False
            elif observation.startswith("[ERROR]") or observation.startswith("[TOOL FAILED") or "tool failed" in lowered:
                status = "failed"
                error_code = "tool_failed"
                retryable = True

        return {
            "schema_version": "loop.observation_evaluation.v1",
            "status": status,
            "error_code": error_code,
            "retryable": retryable,
            "needs_confirmation": needs_confirmation,
            "next_actions": next_actions,
            "summary": summary,
        }

    def _parse_observation_payload(self, observation: str) -> Any:
        try:
            return json.loads(observation)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            return ast.literal_eval(observation)
        except (ValueError, SyntaxError, TypeError):
            return None

    def _summarize_payload(self, payload: Dict[str, Any], fallback: str) -> str:
        parts = []
        for key in ("schema_version", "status", "blocked_reason", "error", "operation"):
            value = payload.get(key)
            if value:
                parts.append(f"{key}={value}")
        return "; ".join(parts) if parts else fallback

    def _round_status_from_evaluation(self, evaluation: Dict[str, Any], success: bool) -> str:
        status = evaluation.get("status", "success" if success else "failed")
        if status in {"blocked", "needs_confirmation", "partial", "failed"}:
            return status
        return "success" if success else "failed"
    
    def run(
        self,
        goal: str,
        system_prompt: str,
        llm_call: Callable[[List[Dict], Any], tuple[str, int]],
        tools: Dict[str, Callable],
        initial_context: Optional[List[Dict]] = None,
        on_round_complete: Optional[Callable[[LoopRound], None]] = None,
    ) -> LoopTrace:
        """
        主循环入口
        
        Args:
            goal: 目标任务描述
            system_prompt: 系统提示
            llm_call: LLM调用函数，签名 fn(messages, tools) -> (response_text, token_cost)
            tools: 可用工具字典 {name: func}
            initial_context: 初始上下文消息
            on_round_complete: 每轮完成后的回调
        
        Returns:
            LoopTrace: 完整的执行轨迹
        """
        # 初始化 Trace
        trace = LoopTrace(
            agent_name=self.agent_name,
            goal=goal,
            metadata={
                "max_rounds": self.max_rounds,
                "dedup_threshold": self.dedup_threshold,
                "token_budget": self.token_budget,
                "state_mode": self.state_mode,
            }
        )
        self.current_trace = trace
        self.call_history = {}
        self.total_tokens_used = 0
        
        # 构建初始消息
        messages = initial_context or []
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": goal},
            ]
        
        print(f"🚀 LoopEngine started: {goal[:80]}...")
        print(f"   Config: max_rounds={self.max_rounds}, dedup={self.dedup_threshold}, "
              f"budget={self.token_budget}, state={self.state_mode}")
        
        for round_idx in range(1, self.max_rounds + 1):
            # 状态管理：裁剪消息
            messages = self._manage_state(messages)
            
            # 调用 LLM
            try:
                response_text, token_cost = llm_call(messages, tools)
            except Exception as e:
                round_record = LoopRound(
                    intent=goal,
                    thought="",
                    action="llm_call",
                    action_input={},
                    observation=f"LLM call failed: {str(e)}",
                    status="failed",
                    token_cost=0,
                )
                trace.rounds.append(round_record)
                trace.status = "failed"
                trace.end_time = datetime.now().isoformat()
                return trace
            
            # 检查预算
            if not self._check_budget(token_cost):
                round_record = LoopRound(
                    intent=goal,
                    thought="",
                    action="budget_check",
                    action_input={},
                    observation=f"Token budget exceeded: {self.total_tokens_used}/{self.token_budget}",
                    status="blocked",
                    token_cost=token_cost,
                )
                trace.rounds.append(round_record)
                trace.status = "budget_exceeded"
                trace.total_token_cost = self.total_tokens_used
                trace.end_time = datetime.now().isoformat()
                print(f"💰 Budget exceeded after {round_idx} rounds")
                return trace
            
            # 解析 LLM 响应（ReAct 格式）
            # 预期格式：Thought: ...\nAction: ...\nAction Input: ...\n 或 直接回答
            thought, action, action_input, is_final = self._parse_response(response_text)
            
            # 重复调用检测
            if action and action != "final_answer":
                if self._check_dedup(action, action_input):
                    # 拦截重复调用
                    dedup_msg = (
                        f"[DEDUP] This call has been attempted {self.dedup_threshold} times already. "
                        f"Tool: {action}, Input: {action_input}. "
                        f"Please change your approach or provide a final conclusion."
                    )
                    round_record = LoopRound(
                        intent=goal,
                        thought=thought,
                        action=action,
                        action_input=action_input,
                        observation=dedup_msg,
                        status="blocked",
                        token_cost=token_cost,
                    )
                    trace.rounds.append(round_record)
                    trace.total_token_cost += token_cost
                    
                    # 把拦截信息反馈给 LLM
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": dedup_msg})
                    
                    if on_round_complete:
                        on_round_complete(round_record)
                    continue
            
            # 如果是最终回答，直接结束
            if is_final or not action:
                round_record = LoopRound(
                    intent=goal,
                    thought=thought,
                    action="final_answer",
                    action_input={},
                    observation=response_text[:500],
                    status="success",
                    token_cost=token_cost,
                )
                trace.rounds.append(round_record)
                trace.total_token_cost += token_cost
                trace.final_result = response_text
                trace.status = "completed"
                trace.end_time = datetime.now().isoformat()
                print(f"✅ Goal completed in {round_idx} rounds")
                return trace
            
            # 执行工具
            observation, success = self._execute_with_retry(action, action_input, tools)
            observation_evaluation = self._evaluate_observation(observation, success)
            
            round_record = LoopRound(
                intent=goal,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation,
                observation_evaluation=observation_evaluation,
                status=self._round_status_from_evaluation(observation_evaluation, success),
                token_cost=token_cost,
            )
            trace.rounds.append(round_record)
            trace.total_token_cost += token_cost
            
            if on_round_complete:
                on_round_complete(round_record)
            
            # 构建下一轮的消息
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            messages.append({
                "role": "user",
                "content": "Observation Evaluation: "
                + json.dumps(observation_evaluation, ensure_ascii=False, sort_keys=True),
            })
            
            print(f"   Round {round_idx}: [{round_record.status}] {action} → {observation[:60]}...")
        
        # 达到最大轮数，仍未完成
        trace.status = "timeout"
        trace.end_time = datetime.now().isoformat()
        trace.final_result = f"[MAX ROUNDS REACHED] The loop reached the maximum of {self.max_rounds} rounds without completing the goal."
        print(f"⏰ Max rounds ({self.max_rounds}) reached")
        return trace
    
    def _parse_response(self, response: str) -> tuple:
        """
        解析 ReAct 格式的 LLM 响应
        
        支持格式：
        Thought: ...
        Action: ...
        Action Input: {...}
        
        或 直接回答（以 Final Answer: 开头）
        """
        thought = ""
        action = ""
        action_input = {}
        is_final = False
        
        # 尝试解析 ReAct 格式
        lines = response.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if line.lower().startswith("thought:"):
                thought = line[len("thought:"):].strip()
            elif line.lower().startswith("action:"):
                action = line[len("action:"):].strip()
            elif line.lower().startswith("action input:"):
                input_str = line[len("action input:"):].strip()
                try:
                    action_input = json.loads(input_str)
                except:
                    action_input = {"raw": input_str}
            elif line.lower().startswith("final answer:"):
                is_final = True
                action = "final_answer"
        
        # 如果没有明确的 Action，但有 Final Answer，标记为完成
        if not action and ("final answer" in response.lower() or "finished" in response.lower()):
            is_final = True
            action = "final_answer"
        
        # 如果完全没有 Action 标记，视为直接回答
        if not action:
            is_final = True
            action = "final_answer"
        
        return thought, action, action_input, is_final


class TokenBudget:
    """Token 预算管理器"""
    
    def __init__(self, total_budget: int, warning_threshold: float = 0.8):
        self.total_budget = total_budget
        self.warning_threshold = warning_threshold
        self.used = 0
        self.history: List[Dict] = []
    
    def consume(self, tokens: int, round_id: str = ""):
        self.used += tokens
        self.history.append({
            "round_id": round_id,
            "tokens": tokens,
            "cumulative": self.used,
            "timestamp": datetime.now().isoformat(),
        })
        
        ratio = self.used / self.total_budget
        if ratio >= 1.0:
            raise BudgetExceededException(
                f"Token budget exceeded: {self.used}/{self.total_budget}"
            )
        elif ratio >= self.warning_threshold:
            print(f"⚠️ Token budget warning: {self.used}/{self.total_budget} ({ratio*100:.1f}%)")
    
    def remaining(self) -> int:
        return max(0, self.total_budget - self.used)
    
    def is_exhausted(self) -> bool:
        return self.used >= self.total_budget
    
    def report(self) -> Dict:
        return {
            "total_budget": self.total_budget,
            "used": self.used,
            "remaining": self.remaining(),
            "usage_ratio": self.used / self.total_budget,
            "history_count": len(self.history),
        }


class BudgetExceededException(Exception):
    """Token 预算超支异常"""
    pass
