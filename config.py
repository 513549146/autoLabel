import os

# Hugging Face 镜像（国内直连 huggingface.co 会失败时自动走镜像）
# 若你的网络可直连 HuggingFace，或想用其他镜像，请修改此处
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 修复畸形的 NO_PROXY（如 "localhost:127.0.0.1"）导致 httpx 解析代理时崩溃
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 模型权重目录
WEIGHTS_DIR = os.path.join(BASE_DIR, "weights")
GROUNDINGDINO_CONFIG = os.path.join(BASE_DIR, "groundingdino", "config", "GroundingDINO_SwinT_OGC.py")
GROUNDINGDINO_WEIGHTS = os.path.join(WEIGHTS_DIR, "groundingdino_swint_ogc.pth")
SAM_WEIGHTS = os.path.join(WEIGHTS_DIR, "sam_hq_vit_h.pth")
# 本地 bert 文本编码器（已下载到项目内，无需联网）
# 该检测权重是用 bert-base-uncased（英文）训练的，请勿更换为其它词表模型
BERT_ENCODER_DIR = os.path.join(WEIGHTS_DIR, "bert-base-uncased")

# 检测模型选择：gd15（Grounding DINO 1.5，推荐）/ gd_ogc（旧版）/ yolo（微调后）
DETECTOR = "gd15"
GD15_DIR = os.path.join(WEIGHTS_DIR, "grounding-dino-base")
YOLO_WEIGHTS = os.path.join(WEIGHTS_DIR, "yolo_best.pt")  # 微调后 YOLO 权重，训练后放在此处

# 数据目录
INPUT_DIR = os.path.join(BASE_DIR, "images")
OUTPUT_DIR = os.path.join(BASE_DIR, "annotations")

# 支持的图片扩展名
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

# 检测阈值
BOX_THRESHOLD = 0.25
TEXT_THRESHOLD = 0.25

# 默认提示词（多个类别用 " . " 分隔，需用英文）
PROMPT = "cat . dog . person"
