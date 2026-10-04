"""Agent settings. Every limit and threshold from the plan lives here.

Values the plan gives a default for use that default. Values the owner must provide ([ASK]) default to
``None``; code that needs them refuses to act and says which value is missing instead of guessing.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PROMOTE_URL = "https://t.me/premierleaguedaily"


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- modes ---
    dry_run: bool = Field(default=True, validation_alias=AliasChoices("ADS_DRY_RUN", "dry_run"))
    db_path: str = Field(default="data/agent.sqlite", validation_alias=AliasChoices("AGENT_DB_PATH", "db_path"))
    # Telegram user ids allowed to accept proposals (I1). Empty = nobody may accept.
    allowed_user_ids_raw: str = Field(
        default="", validation_alias=AliasChoices("BOT_ALLOWED_USER_IDS", "allowed_user_ids_raw")
    )
    # I5: promote_url allow-list (comma-separated).
    promote_url_allowlist_raw: str = Field(
        default=DEFAULT_PROMOTE_URL,
        validation_alias=AliasChoices("AGENT_PROMOTE_URL_ALLOWLIST", "promote_url_allowlist_raw"),
    )
    # [VERIFY] balance semantics, see docs/agent-recon.md section 2.
    balance_semantics: str = Field(
        default="includes_ads",
        validation_alias=AliasChoices("AGENT_BALANCE_SEMANTICS", "balance_semantics"),
    )
    currency: str = Field(default="TON", validation_alias=AliasChoices("AGENT_CURRENCY", "currency"))

    # --- owner inputs [ASK]: no defaults in the plan ---
    hurdle_cpa_ton: float | None = Field(default=None, validation_alias=AliasChoices("HURDLE_CPA_TON", "hurdle_cpa_ton"))
    explore_share: float | None = Field(default=None, validation_alias=AliasChoices("EXPLORE_SHARE", "explore_share"))
    max_loss_per_hypothesis: float | None = Field(
        default=None, validation_alias=AliasChoices("MAX_LOSS_PER_HYPOTHESIS", "max_loss_per_hypothesis")
    )
    max_active_experiments: int | None = Field(
        default=None, validation_alias=AliasChoices("MAX_ACTIVE_EXPERIMENTS", "max_active_experiments")
    )
    first_live_max_budget: float | None = Field(
        default=None, validation_alias=AliasChoices("FIRST_LIVE_MAX_BUDGET", "first_live_max_budget")
    )
    llm_daily_usd_cap: float | None = Field(
        default=None, validation_alias=AliasChoices("LLM_DAILY_USD_CAP", "llm_daily_usd_cap")
    )

    # --- plan defaults ---
    max_pending_proposals: int = Field(default=3, validation_alias=AliasChoices("MAX_PENDING_PROPOSALS", "max_pending_proposals"))
    proposal_ttl_hours: float = Field(default=12.0, validation_alias=AliasChoices("PROPOSAL_TTL_HOURS", "proposal_ttl_hours"))
    expiry_check_seconds: float = Field(default=60.0, validation_alias=AliasChoices("AGENT_EXPIRY_CHECK_SECONDS", "expiry_check_seconds"))
    tau_min: float = Field(default=0.10, validation_alias=AliasChoices("AGENT_TAU_MIN", "tau_min"))
    mc_samples: int = Field(default=4000, validation_alias=AliasChoices("AGENT_MC_SAMPLES", "mc_samples"))
    cold_start_n0: int = Field(default=12, validation_alias=AliasChoices("AGENT_COLD_START_N0", "cold_start_n0"))
    stage1_budget: float = Field(default=0.7, validation_alias=AliasChoices("AGENT_STAGE1_BUDGET", "stage1_budget"))
    stage1_min_views: int = Field(default=5000, validation_alias=AliasChoices("AGENT_STAGE1_MIN_VIEWS", "stage1_min_views"))
    stage2_budget: float = Field(default=3.0, validation_alias=AliasChoices("AGENT_STAGE2_BUDGET", "stage2_budget"))
    stage3_budget: float = Field(default=8.0, validation_alias=AliasChoices("AGENT_STAGE3_BUDGET", "stage3_budget"))
    halving_eta: int = Field(default=3, validation_alias=AliasChoices("AGENT_HALVING_ETA", "halving_eta"))
    kill_if_p_below: float = Field(default=0.05, validation_alias=AliasChoices("AGENT_KILL_IF_P_BELOW", "kill_if_p_below"))
    promote_if_p_above: float = Field(default=0.9, validation_alias=AliasChoices("AGENT_PROMOTE_IF_P_ABOVE", "promote_if_p_above"))
    # Not in the plan: width of the CPM bucket used in the dedup fingerprint (section 9).
    fingerprint_cpm_bucket: float = Field(default=0.05, validation_alias=AliasChoices("AGENT_FINGERPRINT_CPM_BUCKET", "fingerprint_cpm_bucket"))
    dedup_days: int = Field(default=14, validation_alias=AliasChoices("AGENT_DEDUP_DAYS", "dedup_days"))
    cpm_step: float = Field(default=0.01, validation_alias=AliasChoices("AGENT_CPM_STEP", "cpm_step"))
    # Priors (section 7.1): CTR 1.25% with ~1000 views, CVR 6.7% with ~50 clicks.
    prior_ctr: float = Field(default=0.0125, validation_alias=AliasChoices("AGENT_PRIOR_CTR", "prior_ctr"))
    prior_ctr_strength: float = Field(default=1000.0, validation_alias=AliasChoices("AGENT_PRIOR_CTR_STRENGTH", "prior_ctr_strength"))
    prior_cvr: float = Field(default=0.067, validation_alias=AliasChoices("AGENT_PRIOR_CVR", "prior_cvr"))
    prior_cvr_strength: float = Field(default=50.0, validation_alias=AliasChoices("AGENT_PRIOR_CVR_STRENGTH", "prior_cvr_strength"))
    external_prior_sigma: float = Field(default=0.3, validation_alias=AliasChoices("AGENT_EXTERNAL_PRIOR_SIGMA", "external_prior_sigma"))
    likelihood_temper: float = Field(default=0.6, validation_alias=AliasChoices("AGENT_LIKELIHOOD_TEMPER", "likelihood_temper"))
    # Volume constant used in scoring while real volume is unknown (section 7.2/7.3).
    default_volume_views: float = Field(default=10000.0, validation_alias=AliasChoices("AGENT_DEFAULT_VOLUME_VIEWS", "default_volume_views"))

    # Candidate generation (phase 4). Not in the plan; documented in docs/agent-questions.md.
    base_cpm: float | None = Field(default=None, validation_alias=AliasChoices("AGENT_BASE_CPM", "base_cpm"))
    history_top_n: int = Field(default=3, validation_alias=AliasChoices("AGENT_HISTORY_TOP_N", "history_top_n"))
    ledger_top_n: int = Field(default=5, validation_alias=AliasChoices("AGENT_LEDGER_TOP_N", "ledger_top_n"))
    ledger_min_joins: int = Field(default=1, validation_alias=AliasChoices("AGENT_LEDGER_MIN_JOINS", "ledger_min_joins"))
    channel_description: str = Field(default="", validation_alias=AliasChoices("AGENT_CHANNEL_DESCRIPTION", "channel_description"))
    add_control: bool = Field(default=True, validation_alias=AliasChoices("AGENT_ADD_CONTROL", "add_control"))

    # --- files ---
    geo_pool_path: str = Field(default="config/geo_pool.yaml", validation_alias=AliasChoices("AGENT_GEO_POOL_PATH", "geo_pool_path"))
    external_placements_path: str = Field(
        default="data/external_placements.csv",
        validation_alias=AliasChoices("AGENT_EXTERNAL_PLACEMENTS_PATH", "external_placements_path"),
    )
    creatives_path: str = Field(default="config/creatives.yaml", validation_alias=AliasChoices("AGENT_CREATIVES_PATH", "creatives_path"))

    # --- LLM (phase 5) ---
    llm_provider: str = Field(default="anthropic", validation_alias=AliasChoices("LLM_PROVIDER", "llm_provider"))
    llm_model: str | None = Field(default=None, validation_alias=AliasChoices("LLM_MODEL", "llm_model"))
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"), repr=False)
    llm_price_input_per_mtok: float | None = Field(default=None, validation_alias=AliasChoices("LLM_PRICE_INPUT_PER_MTOK", "llm_price_input_per_mtok"))
    llm_price_output_per_mtok: float | None = Field(default=None, validation_alias=AliasChoices("LLM_PRICE_OUTPUT_PER_MTOK", "llm_price_output_per_mtok"))
    llm_price_cache_read_per_mtok: float | None = Field(
        default=None, validation_alias=AliasChoices("LLM_PRICE_CACHE_READ_PER_MTOK", "llm_price_cache_read_per_mtok")
    )
    llm_price_cache_write_per_mtok: float | None = Field(
        default=None, validation_alias=AliasChoices("LLM_PRICE_CACHE_WRITE_PER_MTOK", "llm_price_cache_write_per_mtok")
    )
    llm_price_web_search_per_1k: float | None = Field(
        default=None, validation_alias=AliasChoices("LLM_PRICE_WEB_SEARCH_PER_1K", "llm_price_web_search_per_1k")
    )
    llm_max_tokens: int = Field(default=2048, validation_alias=AliasChoices("LLM_MAX_TOKENS", "llm_max_tokens"))
    llm_web_search_max_uses: int = Field(default=5, validation_alias=AliasChoices("LLM_WEB_SEARCH_MAX_USES", "llm_web_search_max_uses"))
    llm_max_retries: int = Field(default=2, validation_alias=AliasChoices("LLM_MAX_RETRIES", "llm_max_retries"))

    @field_validator(
        "hurdle_cpa_ton",
        "explore_share",
        "max_loss_per_hypothesis",
        "max_active_experiments",
        "first_live_max_budget",
        "llm_daily_usd_cap",
        "base_cpm",
        "llm_model",
        "anthropic_api_key",
        "llm_price_input_per_mtok",
        "llm_price_output_per_mtok",
        "llm_price_cache_read_per_mtok",
        "llm_price_cache_write_per_mtok",
        "llm_price_web_search_per_1k",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return value

    @field_validator("balance_semantics")
    @classmethod
    def _check_semantics(cls, value: str) -> str:
        if value not in {"includes_ads", "excludes_ads"}:
            raise ValueError("AGENT_BALANCE_SEMANTICS must be includes_ads or excludes_ads")
        return value

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(int(part) for part in _split_csv(self.allowed_user_ids_raw))

    @property
    def promote_url_allowlist(self) -> tuple[str, ...]:
        return tuple(_split_csv(self.promote_url_allowlist_raw))

    @property
    def proposal_ttl_seconds(self) -> int:
        return int(self.proposal_ttl_hours * 3600)

    def missing_planner_inputs(self) -> list[str]:
        """Owner inputs the proposal cycle cannot run without."""
        missing = []
        if self.hurdle_cpa_ton is None:
            missing.append("HURDLE_CPA_TON")
        if self.explore_share is None:
            missing.append("EXPLORE_SHARE")
        if self.max_loss_per_hypothesis is None:
            missing.append("MAX_LOSS_PER_HYPOTHESIS")
        if self.max_active_experiments is None:
            missing.append("MAX_ACTIVE_EXPERIMENTS")
        return missing
