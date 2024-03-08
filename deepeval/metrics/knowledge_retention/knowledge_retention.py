from typing import Optional, Union, Dict, List
from pydantic import BaseModel, Field

from deepeval.test_case import ConversationalTestCase
from deepeval.metrics import BaseConversationalMetric
from deepeval.utils import trimAndLoadJson
from deepeval.models import GPTModel, DeepEvalBaseLLM
from deepeval.metrics.knowledge_retention.template import (
    KnowledgeRetentionTemplate,
)
from deepeval.progress_context import metrics_progress_context
from deepeval.telemetry import capture_metric_type


class Knowledge(BaseModel):
    data: Dict[str, Union[str, List[str]]]


class KnowledgeRetentionVerdict(BaseModel):
    index: int
    verdict: str
    reason: str = Field(default=None)


class KnowledgeRetentionMetric(BaseConversationalMetric):
    def __init__(
        self,
        threshold: float = 0.5,
        model: Optional[Union[str, DeepEvalBaseLLM]] = None,
        include_reason: bool = True,
        strict_mode: bool = False,
    ):
        self.threshold = 1 if strict_mode else threshold
        if isinstance(model, DeepEvalBaseLLM):
            self.model = model
        else:
            self.model = GPTModel(model=model)
        self.evaluation_model = self.model.get_model_name()
        self.include_reason = include_reason
        self.strict_mode = strict_mode

    def measure(self, test_case: ConversationalTestCase):
        if len(test_case.messages) == 0:
            raise ValueError("Messages cannot be empty")

        with metrics_progress_context(
            self.__name__,
            self.evaluation_model,
            self.strict_mode,
        ):
            self.knowledges: List[Knowledge] = self._generate_knowledges(
                test_case
            )
            self.verdicts: List[KnowledgeRetentionVerdict] = (
                self._generate_verdicts(test_case)
            )

            knowledge_retention_score = self._calculate_score()
            self.reason = self._generate_reason(knowledge_retention_score)

            self.success = knowledge_retention_score >= self.threshold
            self.score = knowledge_retention_score
            capture_metric_type(self.__name__)
            return self.score

    def _generate_reason(self, score: float) -> str:
        if self.include_reason is False:
            return None

        attritions = []
        for verdict in self.verdicts:
            if verdict.verdict.strip().lower() == "yes":
                attritions.append(verdict.reason)

        prompt: dict = KnowledgeRetentionTemplate.generate_reason(
            attritions=attritions,
            score=format(score, ".2f"),
        )

        res = self.model(prompt)
        return res

    def _calculate_score(self) -> float:
        number_of_verdicts = len(self.verdicts)
        if number_of_verdicts == 0:
            return 0

        retention_count = 0
        for verdict in self.verdicts:
            if verdict.verdict.strip().lower() == "no":
                retention_count += 1

        score = retention_count / number_of_verdicts

        return 0 if self.strict_mode and score < self.threshold else score

    def _generate_verdicts(
        self, test_case: ConversationalTestCase
    ) -> List[KnowledgeRetentionVerdict]:
        verdicts: List[KnowledgeRetentionVerdict] = []
        for index, message in enumerate(test_case.messages):
            previous_knowledge = self.knowledges[index].data

            prompt = KnowledgeRetentionTemplate.generate_verdict(
                llm_message=message.actual_output,
                previous_knowledge=previous_knowledge,
            )
            res = self.model(prompt)
            data = trimAndLoadJson(res)
            verdict = KnowledgeRetentionVerdict(index=index, **data)
            verdicts.append(verdict)

        return verdicts

    def _generate_knowledges(
        self, test_case: ConversationalTestCase
    ) -> List[Knowledge]:
        knowledges: List[Knowledge] = []
        for index, message in enumerate(test_case.messages):
            previous_knowledge = knowledges[-1].data if knowledges else {}
            llm_message = (
                test_case.messages[index - 1].actual_output if index > 0 else ""
            )

            prompt = KnowledgeRetentionTemplate.extract_data(
                llm_message=llm_message,
                user_message=message.input,
                previous_knowledge=previous_knowledge,
            )

            res = self.model(prompt)
            data = trimAndLoadJson(res)
            knowledge = Knowledge(data=data)
            knowledges.append(knowledge)

        return knowledges

    def is_successful(self) -> bool:
        return self.success

    @property
    def __name__(self):
        return "Knowledge Retention"
