"""Channel connectors — Telegram, WhatsApp and web into one pipeline.

Every channel shares :class:`connectors.core.ChannelCore`, which maps an external
conversation to one ADK session and one project workspace and drives the existing
``delivery_pipeline``. Run the service with ``python -m connectors.app``; the
WhatsApp bridge is a separate Node process (``connectors/whatsapp_bridge``).
"""

from .core import ChannelCore
from .identity import Identity, identify

__all__ = ["ChannelCore", "Identity", "identify"]
