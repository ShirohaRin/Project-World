"""外部服务协议与 OneBot（NapCat）QQ 适配器。"""

from .onebot_qq import OneBotQQProvider
from .qq import QQMessage, QQProvider

__all__ = ["OneBotQQProvider", "QQMessage", "QQProvider"]
