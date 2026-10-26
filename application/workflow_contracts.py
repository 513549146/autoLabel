"""Contracts for the image annotation workflow strategies.

Only the quick-labeling strategy is executable today.  The own-model strategy
is deliberately represented as a contract so that its future implementation
does not leak into the fast path or Qt pages.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

import config


class AnnotationWorkflowMode(str, Enum):
    """标注流程模式：当前只有 quick 可执行，own_model 为预留。"""
    QUICK = "quick"
    OWN_MODEL = "own_model"


class RecoveryPolicy(str, Enum):
    """补扫策略：关闭、仅空标注补扫、或每张图片都双模型补扫。"""
    OFF = "off"
    EMPTY_ONLY = "empty_only"
    ALL_IMAGES = "all_images"


@dataclass(frozen=True)
class QuickAnnotationConfig:
    """User-selectable settings for a generic, no-model-required task."""

    prompt: str = config.PROMPT
    primary_provider_id: str = "yoloe"
    model_weights_path: str = ""
    recovery_provider_id: str = "gd15"
    recovery_policy: RecoveryPolicy = RecoveryPolicy.EMPTY_ONLY
    confidence: float = config.BOX_THRESHOLD
    scope: str = "new"

    def normalized(self) -> "QuickAnnotationConfig":
        """规范化用户输入：统一提示词分隔符、夹紧置信度、校验范围与补扫策略。"""
        prompt = " . ".join(part.strip() for part in self.prompt.split(".") if part.strip())
        if not prompt:
            raise ValueError("类别提示词不能为空")
        confidence = max(0.1, min(0.95, float(self.confidence)))
        scope = self.scope if self.scope in {"new", "risk", "review", "all"} else "new"
        policy = RecoveryPolicy(self.recovery_policy)
        recovery = self.recovery_provider_id if policy != RecoveryPolicy.OFF else ""
        weights_path = str(self.model_weights_path or "") if self.primary_provider_id == "custom_yolo" else ""
        return replace(self, prompt=prompt, model_weights_path=weights_path, confidence=confidence, scope=scope, recovery_policy=policy, recovery_provider_id=recovery)


@dataclass(frozen=True)
class QuickAnnotationRequest:
    """一次快速标注请求：项目目录、配置与可选的显式文件列表。"""
    project_root: str
    images_dir: str
    annotations_dir: str
    config: QuickAnnotationConfig
    filenames: tuple[str, ...] = ()

    def with_filenames(self, filenames: list[str] | tuple[str, ...]) -> "QuickAnnotationRequest":
        """派生一个指定文件列表的新请求。"""
        return replace(self, filenames=tuple(filenames))


@dataclass(frozen=True)
class QuickImageResult:
    """单张图片的快速标注结果：候选数、风险分、命中规则与参与模型。"""
    filename: str
    prediction_count: int
    risk_score: int
    risk_rules: tuple[str, ...]
    providers: tuple[str, ...]


@dataclass(frozen=True)
class QuickAnnotationResult:
    """整批快速标注结果，并提供按文件名与高风险统计的便捷视图。"""
    completed: tuple[QuickImageResult, ...]
    stopped: bool

    @property
    def filenames(self) -> tuple[str, ...]:
        """返回本次实际完成的文件名。"""
        return tuple(item.filename for item in self.completed)

    @property
    def high_risk_count(self) -> int:
        """统计风险分达到高风险的图片数量。"""
        return sum(1 for item in self.completed if item.risk_score >= config.REVIEW_HIGH_RISK)


class AnnotationWorkflow(Protocol):
    """Common seam for quick and future own-model implementations."""

    mode: AnnotationWorkflowMode

    def plan(self, request: QuickAnnotationRequest) -> tuple[str, ...]:
        """各流程实现的公共入口：只做校验与选片，不产生副作用。"""
        ...


class QuickAnnotationSettings:
    """Persist quick-mode settings in a project, not in application globals."""

    METADATA_KEY = "quick_annotation_config_v1"

    @classmethod
    def load(cls, project) -> QuickAnnotationConfig:
        """从 metadata 读取设置；字段缺失或损坏时回退默认配置。"""
        if not project:
            return QuickAnnotationConfig()
        try:
            raw = json.loads(project.metadata_value(cls.METADATA_KEY, "{}"))
            if not isinstance(raw, dict):
                raise ValueError
            return QuickAnnotationConfig(
                prompt=str(raw.get("prompt", config.PROMPT)),
                primary_provider_id=str(raw.get("primary_provider_id", "yoloe")),
                model_weights_path=str(raw.get("model_weights_path", "")),
                recovery_provider_id=str(raw.get("recovery_provider_id", "gd15")),
                recovery_policy=RecoveryPolicy(raw.get("recovery_policy", RecoveryPolicy.EMPTY_ONLY.value)),
                confidence=float(raw.get("confidence", config.BOX_THRESHOLD)),
                scope=str(raw.get("scope", "new")),
            ).normalized()
        except (TypeError, ValueError, json.JSONDecodeError):
            return QuickAnnotationConfig()

    @classmethod
    def save(cls, project, settings: QuickAnnotationConfig) -> None:
        """规范化后以 JSON 写入项目 metadata。"""
        normalized = settings.normalized()
        project.set_metadata_value(
            cls.METADATA_KEY,
            json.dumps(
                {
                    "mode": AnnotationWorkflowMode.QUICK.value,
                    "prompt": normalized.prompt,
                    "primary_provider_id": normalized.primary_provider_id,
                    "model_weights_path": normalized.model_weights_path,
                    "recovery_provider_id": normalized.recovery_provider_id,
                    "recovery_policy": normalized.recovery_policy.value,
                    "confidence": normalized.confidence,
                    "scope": normalized.scope,
                },
                ensure_ascii=False,
            ),
        )


class OwnModelWorkflowPlaceholder:
    """Reserved integration point for the future project-owned model flow."""

    mode = AnnotationWorkflowMode.OWN_MODEL

    def plan(self, request: QuickAnnotationRequest) -> tuple[str, ...]:
        """预留接口：自有模型流程尚未实现，调用即抛 NotImplementedError。"""
        raise NotImplementedError("自有模型模式尚未接入；请使用快速标注模式")
