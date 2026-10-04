"""Proposal agent for Telegram Ads.

Layer rule: ``agent`` imports ``ads_api`` and never imports ``bot``. Only ``agent.executor`` may call
Ads API methods that change state.
"""
