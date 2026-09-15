# ====================================================================
# 文件：embedding.py
# 用途：向量嵌入模块——使用 bge-m3 模型将实体文本编码为向量
# 所属层级：tcm_kg.pipeline 嵌入层（供 align.py 召回与 HNSW 向量索引用）
# 核心功能：
#   1. Embedder 协议：batch encode → L2 归一化向量矩阵（n, dim）
#   2. BgeM3Embedder：FlagEmbedding 官方 FlagModel，dense 1024 维（ADR-004）
#   3. 批量编码 + L2 归一化（行向量单位长度，便于余弦相似度计算）
#   4. 供 HNSW 向量索引做语义检索（align 阶段召回 + 图谱检索）
# 如何使用：
#   from tcm_kg.pipeline.embedding import build_embedder_from_settings
#   embedder = build_embedder_from_settings()
#   vectors = embedder.encode(["黄芪", "人参"])  # shape=(2, 1024)
# ====================================================================
"""嵌入器抽象（C6.1）：协议 + bge-m3 实现（FlagEmbedding 官方库）。

- Embedder 协议：batch encode → 归一化矩阵（测试注入 FakeEmbedder）；
- BgeM3Embedder：FlagModel（dense 1024 维，ADR-004）；本地目录缺失
  sentence-transformers 的 1_Pooling 子目录，故走 FlagEmbedding 加载。
"""

from typing import Protocol  # 结构化协议（嵌入器接口定义）

import numpy as np  # 向量矩阵运算


# 嵌入协议：文本批 → L2 归一化向量矩阵（n, dim），测试可注入 FakeEmbedder
class Embedder(Protocol):
    """嵌入协议：文本批 → L2 归一化向量矩阵（n, dim）。"""

    dim: int  # 输出向量维度（bge-m3 dense 为 1024）

    def encode(self, texts: list[str]) -> np.ndarray:
        """批量编码；返回 float32 归一化矩阵（行向量单位长度）。"""
        ...


class BgeM3Embedder:
    """bge-m3（BAAI 官方 FlagModel，dense 表示）。"""

    def __init__(self, model_path: str, device: str = "cpu", use_fp16: bool = False):
        from FlagEmbedding import FlagModel  # type: ignore[import-untyped]

        # 加载本地 bge-m3 模型（FlagModel 官方入口）
        self._model = FlagModel(model_path, use_fp16=use_fp16, device=device)
        self.dim = 1024  # dense 向量维度（ADR-004 选定 bge-m3）

    # 批量编码文本 → L2 归一化 float32 矩阵（行向量单位长度，便于余弦相似度）
    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:  # 空输入直接返回
            return np.zeros((0, self.dim), dtype=np.float32)  # (0, dim) 空矩阵
        vectors = np.asarray(self._model.encode(texts), dtype=np.float32)  # 批量编码为 float32
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)  # 逐行计算 L2 范数
        normalized = vectors / np.clip(norms, 1e-12, None)  # 除以范数（下限 1e-12 防除零）
        return np.asarray(normalized, dtype=np.float32)  # 单位行向量矩阵（点积即余弦）


# 从 EmbeddingSettings 构造嵌入器（不经全局 Settings，避免强制 LLM 配置）
# 离线工具（align）无需 LLM，故单独读取嵌入配置
def build_embedder_from_settings() -> BgeM3Embedder:
    """构造嵌入器。

    直接用 EmbeddingSettings（含本地模型默认值），不经全局 Settings——
    后者强制 LLM 三项必填，而离线工具（align）无需 LLM。
    """
    from tcm_kg.core.settings import load_embedding_settings  # 嵌入配置加载（延迟导入）

    emb = load_embedding_settings()  # 读取嵌入配置（模型路径/设备）
    return BgeM3Embedder(model_path=emb.model_name, device=emb.device)  # 构造嵌入器实例
