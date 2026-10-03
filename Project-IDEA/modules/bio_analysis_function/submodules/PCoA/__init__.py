"""PCoA：主坐标分析（经典多维标度）。"""

from .algorithm import PCoAResult, fit_pcoa
from .distance import pairwise_bray_curtis, pairwise_euclidean
from .io import DistanceMatrix, load_distance_matrix
from .report import build_coordinate_plot

__all__ = [
    "PCoAResult",
    "fit_pcoa",
    "DistanceMatrix",
    "load_distance_matrix",
    "pairwise_euclidean",
    "pairwise_bray_curtis",
    "build_coordinate_plot",
]
