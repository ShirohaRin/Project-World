"""Realtime voice dialogue module.

The module owns the audio channel, recognition, endpointing, synthesis and
interruption handling. Text turns are delegated to the existing server-side
dialogue implementation. See ``实时语音对话模块设计.md`` for the design and
``contract/protocols.py`` for the wire contract.
"""
