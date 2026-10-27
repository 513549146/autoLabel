"""Workflow presets, validated graphs, and project-scoped draft/application state."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowStepTemplate:
    id: str
    title: str
    detail: str
    optional: bool = False


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    title: str
    subtitle: str
    executable: bool
    steps: tuple[WorkflowStepTemplate, ...]
    edges: tuple[tuple[str, str], ...]


QUICK_WORKFLOW = WorkflowTemplate(
    id="quick", title="快速标注", subtitle="全图双模型 · 大图切片 · 候选并集 · 人工复核", executable=True,
    steps=(
        WorkflowStepTemplate("project_images", "项目图片", "导入或已有图片"),
        WorkflowStepTemplate("target_queue", "目标队列", "未标注 / 风险 / 待审核"),
        WorkflowStepTemplate("image_check", "图片检查", "文件可读性校验"),
        WorkflowStepTemplate("primary_model", "主模型检测", "开放词表候选"),
        WorkflowStepTemplate("recovery_model", "召回补扫", "默认每张图运行第二模型", optional=True),
        WorkflowStepTemplate("tile_scan", "大图切片补扫", "长边 ≥1600px 时启用模型重叠切片"),
        WorkflowStepTemplate("class_alignment", "类别对齐", "映射项目类别，保留未知类别"),
        WorkflowStepTemplate("candidate_fusion", "候选融合", "保留候选并集，谨慎去重"),
        WorkflowStepTemplate("review_triage", "分歧标记", "单模型和仅切片候选优先复核"),
        WorkflowStepTemplate("risk_and_version", "风险与版本", "质量规则、XML 快照"),
        WorkflowStepTemplate("human_review", "人工复核", "自动进入审核队列"),
    ),
    edges=(
        ("project_images", "target_queue"), ("target_queue", "image_check"),
        ("image_check", "primary_model"), ("primary_model", "recovery_model"),
        ("primary_model", "tile_scan"), ("recovery_model", "tile_scan"),
        ("tile_scan", "class_alignment"), ("class_alignment", "candidate_fusion"),
        ("candidate_fusion", "review_triage"), ("review_triage", "risk_and_version"),
        ("risk_and_version", "human_review"),
    ),
)

OWN_MODEL_WORKFLOW = WorkflowTemplate(
    id="own_model", title="自有模型", subtitle="项目模型 · 补盲模型 · 规则复核", executable=False,
    steps=(
        WorkflowStepTemplate("project_images", "项目图片", "相机或批量数据"),
        WorkflowStepTemplate("target_queue", "任务队列", "按批次、ROI、优先级"),
        WorkflowStepTemplate("image_check", "图片检查", "清晰度与 ROI"),
        WorkflowStepTemplate("own_model", "自有模型", "企业微调模型"),
        WorkflowStepTemplate("supplement_model", "补盲模型", "开放词表补召回", optional=True),
        WorkflowStepTemplate("candidate_fusion", "候选融合", "模型来源可追溯"),
        WorkflowStepTemplate("business_rules", "规则 / 异常", "数量、位置、尺寸规则"),
        WorkflowStepTemplate("human_review", "人工复核", "风险分流与反馈训练"),
    ),
    edges=(
        ("project_images", "target_queue"), ("target_queue", "image_check"),
        ("image_check", "own_model"), ("own_model", "supplement_model"),
        ("own_model", "candidate_fusion"), ("supplement_model", "candidate_fusion"),
        ("candidate_fusion", "business_rules"), ("business_rules", "human_review"),
    ),
)

CUSTOM_WORKFLOW = WorkflowTemplate(
    id="custom", title="自定义流程", subtitle="通用模型 · 可选补扫与候选边界整理 · 人工复核", executable=True,
    steps=(
        WorkflowStepTemplate("project_images", "项目图片", "导入或已有图片"),
        WorkflowStepTemplate("target_queue", "目标队列", "按处理范围选取图片"),
        WorkflowStepTemplate("image_check", "图片检查", "文件可读性校验"),
        WorkflowStepTemplate("primary_model", "主模型检测", "开放词表候选"),
        WorkflowStepTemplate("recovery_model", "召回补扫", "空标注或全量补扫", optional=True),
        WorkflowStepTemplate("tile_scan", "大图切片补扫", "长边 ≥1600px 时启用模型重叠切片"),
        WorkflowStepTemplate("class_alignment", "类别对齐", "映射项目类别，保留未知类别"),
        WorkflowStepTemplate("candidate_fusion", "候选融合", "保留候选并集，谨慎去重"),
        WorkflowStepTemplate("review_triage", "分歧标记", "单模型和仅切片候选优先复核"),
        WorkflowStepTemplate("bounds_check", "边界整理", "裁剪越界框，过滤无效框", optional=True),
        WorkflowStepTemplate("risk_and_version", "风险与版本", "质量规则、XML 快照"),
        WorkflowStepTemplate("human_review", "人工复核", "自动进入审核队列"),
    ),
    edges=(
        ("project_images", "target_queue"), ("target_queue", "image_check"),
        ("image_check", "primary_model"), ("primary_model", "recovery_model"),
        ("primary_model", "tile_scan"), ("recovery_model", "tile_scan"),
        ("tile_scan", "class_alignment"), ("class_alignment", "candidate_fusion"),
        ("candidate_fusion", "review_triage"),
        ("review_triage", "bounds_check"), ("review_triage", "risk_and_version"),
        ("bounds_check", "risk_and_version"), ("risk_and_version", "human_review"),
    ),
)


class WorkflowTemplateRegistry:
    _templates = {item.id: item for item in (QUICK_WORKFLOW, OWN_MODEL_WORKFLOW, CUSTOM_WORKFLOW)}

    def templates(self) -> tuple[WorkflowTemplate, ...]:
        return tuple(self._templates.values())

    def get(self, template_id: str) -> WorkflowTemplate:
        return self._templates.get(template_id, QUICK_WORKFLOW)


@dataclass(frozen=True)
class WorkflowGraph:
    """A constrained DAG with direct links for mandatory operation contracts."""

    template_id: str
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]

    @classmethod
    def preset(cls, template: WorkflowTemplate) -> "WorkflowGraph":
        return cls(template.id, tuple(step.id for step in template.steps), template.edges)

    def validate(self, template: WorkflowTemplate) -> tuple[str, ...]:
        errors: list[str] = []
        known = {step.id: step for step in template.steps}
        nodes = set(self.nodes)
        if self.template_id != template.id:
            errors.append("流程类型与预设不匹配")
        if len(self.nodes) != len(nodes) or nodes - known.keys():
            errors.append("流程包含重复或未知步骤")
        if any(step.id not in nodes for step in template.steps if not step.optional):
            errors.append("必经步骤不能删除")
        edges = set(self.edges)
        if len(edges) != len(self.edges):
            errors.append("流程包含重复连线")
        if any(source not in nodes or target not in nodes or source == target for source, target in edges):
            errors.append("连线端点无效")
        required = {
            edge for edge in template.edges
            if not known[edge[0]].optional and not known[edge[1]].optional
        }
        if not required.issubset(edges):
            errors.append("必经步骤之间的依赖不能断开")
        for step in template.steps:
            if step.optional and step.id in nodes:
                branch = {edge for edge in template.edges if step.id in edge}
                if not branch.issubset(edges):
                    errors.append(f"“{step.title}”需要保留输入和输出连线")
        incoming = {node: 0 for node in nodes}
        followers = {node: [] for node in nodes}
        for source, target in edges:
            if source in nodes and target in nodes:
                incoming[target] += 1
                followers[source].append(target)
        queue = [node for node, count in incoming.items() if count == 0]
        visited = 0
        while queue:
            source = queue.pop()
            visited += 1
            for target in followers[source]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    queue.append(target)
        if visited != len(nodes):
            errors.append("流程存在循环依赖")
        order = {step.id: index for index, step in enumerate(template.steps)}
        if any(order.get(source, -1) >= order.get(target, -1) for source, target in edges):
            errors.append("连线必须按步骤的数据流方向连接")
        return tuple(dict.fromkeys(errors))

    def without_optional(self, template: WorkflowTemplate, step_id: str) -> "WorkflowGraph":
        if not any(step.id == step_id and step.optional for step in template.steps):
            raise ValueError("只能移除可选步骤")
        return WorkflowGraph(self.template_id, tuple(node for node in self.nodes if node != step_id),
                             tuple(edge for edge in self.edges if step_id not in edge))

    def with_optional(self, template: WorkflowTemplate, step_id: str) -> "WorkflowGraph":
        if not any(step.id == step_id and step.optional for step in template.steps):
            raise ValueError("只能添加可选步骤")
        nodes = tuple(step.id for step in template.steps if step.id in self.nodes or step.id == step_id)
        combined = set(self.edges) | {edge for edge in template.edges if step_id in edge}
        edges = tuple(edge for edge in template.edges if edge in combined)
        edges += tuple(edge for edge in self.edges if edge not in template.edges)
        return WorkflowGraph(self.template_id, nodes, edges)


@dataclass
class WorkflowProjectState:
    applied_template_id: str = QUICK_WORKFLOW.id
    draft_template_id: str = QUICK_WORKFLOW.id
    layouts: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    drafts: dict[str, WorkflowGraph] = field(default_factory=dict)
    applied_graph: WorkflowGraph = field(default_factory=lambda: WorkflowGraph.preset(QUICK_WORKFLOW))
    quick_recovery_policy: str | None = None

    def draft(self, template: WorkflowTemplate) -> WorkflowGraph:
        return self.drafts.get(template.id, WorkflowGraph.preset(template))


class WorkflowComposerSettings:
    """Only Apply replaces the executable graph; drafts and positions are separate."""

    METADATA_KEY = "workflow_composer_v2"
    LEGACY_KEY = "workflow_composer_v1"

    @classmethod
    def load(cls, project) -> WorkflowProjectState:
        state = WorkflowProjectState()
        if not project:
            return state
        try:
            raw = json.loads(project.metadata_value(cls.METADATA_KEY, "{}"))
            if not raw:
                legacy = json.loads(project.metadata_value(cls.LEGACY_KEY, "{}"))
                if isinstance(legacy, dict):
                    raw = {"layouts": legacy.get("layouts", {})}
            if not isinstance(raw, dict):
                return state
            registry = WorkflowTemplateRegistry()
            state.applied_template_id = str(raw.get("applied_template", "quick"))
            if state.applied_template_id not in registry._templates:
                state.applied_template_id = "quick"
            state.draft_template_id = str(raw.get("draft_template", state.applied_template_id))
            if state.draft_template_id not in registry._templates:
                state.draft_template_id = "quick"
            policy = raw.get("quick_recovery_policy")
            if policy in ("empty_only", "all_images"):
                state.quick_recovery_policy = policy
            for key, nodes in (raw.get("layouts") or {}).items():
                if key in registry._templates and isinstance(nodes, dict):
                    state.layouts[key] = {
                        str(node): (int(point[0]), int(point[1]))
                        for node, point in nodes.items()
                        if isinstance(point, (list, tuple)) and len(point) == 2
                    }
            for key, graph_data in (raw.get("drafts") or {}).items():
                if key in registry._templates:
                    graph = cls._decode_graph(key, graph_data)
                    if graph:
                        upgraded = cls._upgrade_graph(registry.get(key), graph)
                        if upgraded and not upgraded.validate(registry.get(key)):
                            state.drafts[key] = upgraded
                            if upgraded != graph:
                                state.layouts.pop(key, None)
            graph = cls._decode_graph(state.applied_template_id, raw.get("applied_graph"))
            if graph:
                template = registry.get(state.applied_template_id)
                upgraded = cls._upgrade_graph(template, graph)
                if upgraded and not upgraded.validate(template):
                    state.applied_graph = upgraded
                    if upgraded != graph:
                        state.layouts.pop(template.id, None)
        except (AttributeError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            return WorkflowProjectState()
        return state

    @staticmethod
    def _upgrade_graph(template: WorkflowTemplate, graph: WorkflowGraph) -> WorkflowGraph | None:
        """Insert newly mandatory stages into saved v2 quick/custom diagrams."""
        added = {"tile_scan", "class_alignment", "review_triage"}
        if template.id not in ("quick", "custom") or added.issubset(graph.nodes):
            return graph
        known = {step.id for step in template.steps}
        if graph.template_id != template.id or not set(graph.nodes).issubset(known):
            return None
        upgraded = WorkflowGraph.preset(template)
        for step in template.steps:
            if step.optional and step.id not in graph.nodes:
                upgraded = upgraded.without_optional(template, step.id)
        return upgraded

    @classmethod
    def _decode_graph(cls, key: str, data) -> WorkflowGraph | None:
        if not isinstance(data, dict):
            return None
        try:
            nodes = tuple(str(node) for node in data["nodes"])
            edges = tuple((str(edge[0]), str(edge[1])) for edge in data["edges"])
            return WorkflowGraph(key, nodes, edges)
        except (TypeError, KeyError, IndexError):
            return None

    @classmethod
    def save(cls, project, state: WorkflowProjectState) -> None:
        def encode(graph: WorkflowGraph) -> dict:
            return {"nodes": list(graph.nodes), "edges": [list(edge) for edge in graph.edges]}
        project.set_metadata_value(cls.METADATA_KEY, json.dumps({
            "applied_template": state.applied_template_id,
            "draft_template": state.draft_template_id,
            "quick_recovery_policy": state.quick_recovery_policy,
            "applied_graph": encode(state.applied_graph),
            "drafts": {key: encode(graph) for key, graph in state.drafts.items()},
            "layouts": {
                key: {node: list(point) for node, point in positions.items()}
                for key, positions in state.layouts.items()
            },
        }, ensure_ascii=False))
