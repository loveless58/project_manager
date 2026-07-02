from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass
class PolicyDecision:
    text: str


class SkillPolicy(Protocol):
    name: str

    def plan(
        self,
        goal: str,
        executed: List[str],
        last_observation: Optional[str],
    ) -> Optional[PolicyDecision]:
        ...
