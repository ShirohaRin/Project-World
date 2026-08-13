"""
IDEA-Reasearcher — 科研特化智能体
"""

import logging
import time

logger = logging.getLogger("idea.researcher")


class Researcher:
    def __init__(self, memory, config: dict):
        self.memory = memory
        self.config = config

    async def respond(self, message: str, history: list) -> str:
        """
        被 IDEA 调度时的主要入口。
        根据消息内容判断是文献综述、数据分析还是实验设计。
        """
        self.memory.log_audit("Researcher", "respond", message[:100])
        msg_lower = message.lower()

        if any(w in msg_lower for w in ["实验", "experiment", "设计实验", "对照"]):
            design = await self.experiment_design(
                experiment_goal=message,
            )
            return (
                "这是一个好问题。让我来设计实验方案。\n\n"
                f"## 实验设计\n\n"
                f"**实验目标**：{design['experiment_goal'][:200]}\n\n"
                f"**设计类型**：{design['design_type']}\n\n"
                f"### 实验流程\n"
                + "\n".join(design.get("procedure", []))
                + f"\n\n### 样本计划\n"
                f"- 功效分析建议：{design['sample_plan']['power_analysis']}\n"
                f"- 分组方式：{design['sample_plan']['group_assignment']}\n\n"
                f"### ⚠️ 伦理注意事项\n"
                + "\n".join(f"- {e}" for e in design.get("ethics_considerations", []))
                + "\n\n需要说明的是，以上是实验设计框架。具体参数（如样本量、效应量估计）需要在获取更多信息后精确计算。"
            )

        if any(w in msg_lower for w in ["数据", "data", "统计", "分析", "图表"]):
            analysis = await self.data_analysis(
                analysis_type="eda",
                data_description=message,
            )
            return (
                "让我来分析一下……\n\n"
                f"## 数据分析方案\n\n"
                f"**分析类型**：探索性数据分析\n\n"
                f"### 推荐方法\n"
                + "\n".join(f"- {m}" for m in analysis.get("methodology", []))
                + f"\n\n### 质量检查清单\n"
                + "\n".join(f"- ✅ {c}" for c in analysis.get("quality_checks", []))
                + f"\n\n### 输出格式\n"
                f"- 文本：{analysis['output_format']['text']}\n"
                f"- 代码：{analysis['output_format']['code']}\n\n"
                "需要说明的是，现有证据表明数据的质量决定了分析的上限。建议在分析前做好完整的缺失值处理和异常值检测。"
            )

        # 默认：文献综述
        review = await self.literature_review(
            research_question=message,
        )
        return (
            "这是一个好问题。让我为你梳理相关的文献和证据。\n\n"
            f"## 文献综述\n\n"
            f"**研究问题**：{review['research_question'][:200]}\n\n"
            f"### 检索策略\n"
            f"- 数据库：{' / '.join(review['search_strategy']['databases'])}\n"
            f"- 关键词：{', '.join(review['search_strategy']['keywords'])}\n"
            f"- 纳入标准：{review['search_strategy']['inclusion_criteria']}\n\n"
            f"### 综述结构\n"
            + "\n".join(f"- {s}" for s in review["review_structure"].values())
            + f"\n\n### 来源可信度分级\n"
            f"| Tier | 来源类型 |\n"
            f"|------|----------|\n"
            f"| A | 顶级期刊/会议（Nature, Science, NeurIPS 等） |\n"
            f"| B | 知名期刊/会议、权威机构报告 |\n"
            f"| C | arXiv 预印本（经同行评议者优先） |\n"
            f"| D | 技术博客、白皮书 |\n\n"
            "请注意：以上是文献综述的框架。实际检索和引用需要接入网络搜索工具来获取最新文献。"
            "在任何情况下，我**不**会伪造或捏造引用。所有事实性的主张必须有来源支撑。"
        )

    async def literature_review(self, research_question: str, domains: list = None, max_sources: int = 20) -> dict:
        self.memory.log_audit("Researcher", "literature_review", research_question[:100])
        return {
            "research_question": research_question,
            "domains": domains or ["通用"],
            "max_sources": max_sources,
            "search_strategy": {
                "databases": ["arXiv", "Google Scholar", "IEEE Xplore", "ACM Digital Library"],
                "keywords": self._generate_search_terms(research_question),
                "inclusion_criteria": "同行评审优先、2019年以后、中英文文献",
                "exclusion_criteria": "非学术来源、评论性文章、已撤稿论文",
            },
            "review_structure": {
                "section_1": "研究背景与意义",
                "section_2": "按主题分类的文献分析",
                "section_3": "研究方法对比",
                "section_4": "研究空白与争议",
                "section_5": "结论与未来方向",
            },
        }

    async def data_analysis(self, analysis_type: str, data_description: str, hypothesis: str = "") -> dict:
        self.memory.log_audit("Researcher", "data_analysis", analysis_type)
        return {
            "analysis_type": analysis_type,
            "methodology": [
                "描述性统计（均值/中位数/标准差/分位数）",
                "数据可视化（分布图/箱线图/相关性热图）",
                "缺失值分析与处理策略",
                "异常值检测（IQR / Z-score）",
            ],
            "quality_checks": [
                "数据正态性检验",
                "方差齐性检验",
                "多重共线性检查（VIF）",
                "样本代表性评估",
            ],
            "output_format": {
                "text": "结构化分析报告（含图表说明和统计检验结果）",
                "code": "Python/R 分析脚本（Jupyter Notebook 格式）",
                "data": "分析结果汇总表",
            },
        }

    async def experiment_design(self, experiment_goal: str, variables: str = "", constraints: str = "") -> dict:
        self.memory.log_audit("Researcher", "experiment_design", experiment_goal[:100])
        return {
            "experiment_goal": experiment_goal,
            "design_type": "根据目标在以下类型中选择：RCT（随机对照）/ 准实验 / A/B 测试 / 消融实验",
            "procedure": [
                "1. 明确研究假设（H₀ 和 H₁）",
                "2. 确定自变量和因变量",
                "3. 计算所需样本量（功效 ≥ 0.80, α = 0.05）",
                "4. 随机分组（或匹配分组）",
                "5. 执行预实验验证流程",
                "6. 正式实验数据收集",
                "7. 统计分析并报告效应量",
                "8. 讨论局限性和替代解释",
            ],
            "sample_plan": {
                "power_analysis": "建议功效 ≥ 0.80，α = 0.05",
                "minimum_sample_size": "需根据预期效应量通过 G*Power 或 simr 计算",
                "group_assignment": "完全随机 / 分层随机 / 匹配分组",
            },
            "ethics_considerations": [
                "是否需要伦理委员会批准？",
                "参与者知情同意流程",
                "数据匿名化和隐私保护",
                "是否存在利益冲突？",
            ],
        }

    def _generate_search_terms(self, question: str) -> list:
        words = question.replace("？", "").replace("?", "").split()
        return [w for w in words if len(w) >= 3][:6] or ["待生成具体检索词"]
