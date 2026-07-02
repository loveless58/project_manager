from typing import List, Optional

from .base import PolicyDecision


class CloudCCCrmPolicy:
    name = "cloudcc_crm"

    def plan(
        self,
        goal: str,
        executed: List[str],
        last_observation: Optional[str],
    ) -> Optional[PolicyDecision]:
        if "cloudcc_session_probe" not in executed:
            return PolicyDecision(
                "Thought: CRM/CloudCC 操作必须先验证登录态和浏览器适配器。\n"
                "Action: cloudcc_session_probe\n"
                "Action Input: {}"
            )

        if last_observation and any(k in last_observation for k in ["browser_adapter_unavailable", "login_required", "Login | CloudCC"]):
            if any(k in goal for k in ["草稿", "录入", "准备"]):
                if "cloudcc_prepare_opportunity_draft" not in executed:
                    return PolicyDecision(
                        "Thought: CloudCC 浏览器不可用，但仍可基于已知上下文准备本地 CRM 草稿，不执行写入。\n"
                        "Action: cloudcc_prepare_opportunity_draft\n"
                        "Action Input: {\"bid_context\": {}}"
                    )
                if "cloudcc_fill_draft_gated" not in executed:
                    return PolicyDecision(
                        "Thought: CRM 草稿需要提交前确认，使用受控门控工具返回 needs_confirmation。\n"
                        "Action: cloudcc_fill_draft_gated\n"
                        "Action Input: {\"draft\": {}}"
                    )
                return PolicyDecision("Final Answer: CRM 草稿已准备为受控待确认状态；未执行 CloudCC 写入。")
            return PolicyDecision(
                "Final Answer: CloudCC/CRM 当前被阻塞：浏览器适配器或登录态不可用。不能把 blocked 解释为未查到重复。"
            )

        if any(k in goal for k in ["查重", "重复", "已有", "是否存在"]):
            if "cloudcc_duplicate_check" not in executed:
                return PolicyDecision(
                    "Thought: 登录态可用后，执行 CloudCC 商机只读查重。\n"
                    "Action: cloudcc_duplicate_check\n"
                    "Action Input: {}"
                )
            return PolicyDecision("Final Answer: CloudCC 商机查重流程已完成，请以工具 evidence 中的结论为准。")

        if any(k in goal for k in ["草稿", "录入", "准备"]):
            if "cloudcc_prepare_opportunity_draft" not in executed:
                return PolicyDecision(
                    "Thought: 准备 CRM 商机草稿，不写入 CloudCC。\n"
                    "Action: cloudcc_prepare_opportunity_draft\n"
                    "Action Input: {\"bid_context\": {}}"
                )
            if "cloudcc_fill_draft_gated" not in executed:
                return PolicyDecision(
                    "Thought: 填充 CRM 草稿必须停在提交前，等待人工确认。\n"
                    "Action: cloudcc_fill_draft_gated\n"
                    "Action Input: {\"draft\": {}}"
                )
            return PolicyDecision("Final Answer: CRM 草稿已进入提交前确认状态。")

        return PolicyDecision("Final Answer: 请明确 CloudCC/CRM 需求：只读查重、记录查询、草稿准备或提交前确认。")
