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
    QUICK = "quick"
    OWN_MODEL = "own_model"


class RecoveryPolicy(str, Enum):
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
    recovery_policy: RecoveryPolicy = RecoveryPolicy.ALL_IMAGES
    confidence: float = config.HIGH_RECALL_THRESHOLD
    scope: str = "new"
    tiled_inference: bool = False

    def normalized(self) -> "QuickAnnotationConfig":
        prompt = " . ".join(part.strip() for part in self.prompt.split(".") if part.strip())
        if not prompt:
            raise ValueError("类别提示词不能为空")
        confidence = max(0.1, min(0.95, float(self.confidence)))
        scope = self.scope if self.scope in {"new", "risk", "review", "all"} else "new"
        policy = RecoveryPolicy(self.recovery_policy)
        recovery = self.recovery_provider_id if policy != RecoveryPolicy.OFF else ""
        weights_path = str(self.model_weights_path or "") if self.primary_provider_id in {"custom_yolo", "yoloe"} else ""
        return replace(self, prompt=prompt, model_weights_path=weights_path, confidence=confidence, scope=scope, recovery_policy=policy, recovery_provider_id=recovery, tiled_inference=bool(self.tiled_inference))


@dataclass(frozen=True)
class QuickAnnotationRequest:
    project_root: str
    images_dir: str
    annotations_dir: str
    config: QuickAnnotationConfig
    filenames: tuple[str, ...] = ()

    def with_filenames(self, filenames: list[str] | tuple[str, ...]) -> "QuickAnnotationRequest":
        return replace(self, filenames=tuple(filenames))


@dataclass(frozen=True)
class QuickImageResult:
    filename: str
    prediction_count: int
    risk_score: int
    risk_rules: tuple[str, ...]
    providers: tuple[str, ...]


@dataclass(frozen=True)
class QuickAnnotationResult:
    completed: tuple[QuickImageResult, ...]
    stopped: bool
    failed: tuple[str, ...] = ()

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(item.filename for item in self.completed)

    @property
    def high_risk_count(self) -> int:
        return sum(1 for item in self.completed if item.risk_score >= config.REVIEW_HIGH_RISK)


class AnnotationWorkflow(Protocol):
    """Common seam for quick and future own-model implementations."""

    mode: AnnotationWorkflowMode

    def plan(self, request: QuickAnnotationRequest) -> tuple[str, ...]:
        ...


class QuickAnnotationSettings:
    """Persist quick-mode settings in a project, not in application globals."""

    METADATA_KEY = "quick_annotation_config_v1"

    @classmethod
    def load(cls, project) -> QuickAnnotationConfig:
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
                recovery_policy=RecoveryPolicy(raw.get("recovery_policy", RecoveryPolicy.ALL_IMAGES.value)),
                confidence=float(raw.get("confidence", config.HIGH_RECALL_THRESHOLD)),
                scope=str(raw.get("scope", "new")),
                tiled_inference=bool(raw.get("tiled_inference", False)),
            ).normalized()
        except (TypeError, ValueError, json.JSONDecodeError):
            return QuickAnnotationConfig()

    @classmethod
    def save(cls, project, settings: QuickAnnotationConfig) -> None:
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
                    "tiled_inference": normalized.tiled_inference,
                },
                ensure_ascii=False,
            ),
        )


class OwnModelWorkflowPlaceholder:
    """Reserved integration point for the future project-owned model flow."""

    mode = AnnotationWorkflowMode.OWN_MODEL

    def plan(self, request: QuickAnnotationRequest) -> tuple[str, ...]:
        raise NotImplementedError("自有模型模式尚未接入；请使用快速标注模式")
