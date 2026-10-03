"""PCA：通用数值矩阵主成分分析。"""

from .algorithm import PCAResult, fit_pca
from .io import MatrixTable, load_feature_matrix
from .report import build_score_plot, rank_loadings

__all__ = [
    "PCAResult",
    "fit_pca",
    "MatrixTable",
    "load_feature_matrix",
    "build_score_plot",
    "rank_loadings",
]
