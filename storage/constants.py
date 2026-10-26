"""存储层共用的文件名和审核状态常量；修改状态值需考虑旧数据库。"""

PROJECT_FILE = ".autolabel-project.json"
WORKBENCH_FILE = "autolabel.project.json"
DATABASE_FILE = "autolabel.db"
WORKSPACE_DATABASE_FILE = "workspace.db"
ANNOTATION_VERSION_DIR = "annotation_versions"
QUALITY_RULES_VERSION = 1
STATUS_UNREVIEWED = "unreviewed"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_APPROVED = "approved"
STATUS_SKIPPED = "skipped"
STATUSES = (STATUS_UNREVIEWED, STATUS_NEEDS_REVIEW, STATUS_APPROVED, STATUS_SKIPPED)

STATUS_LABELS = {
    STATUS_UNREVIEWED: "未标注",
    STATUS_NEEDS_REVIEW: "待审核",
    STATUS_APPROVED: "已通过",
    STATUS_SKIPPED: "已跳过",
}
