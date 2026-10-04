-- Section 6 schema of the agent plan. Money in account currency (TON), times in unix seconds.

CREATE TABLE account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    account_id TEXT,
    currency TEXT NOT NULL,
    spent_budget REAL NOT NULL,
    remaining_budget REAL NOT NULL,
    ads_budget REAL NOT NULL
);
CREATE INDEX account_snapshots_ts ON account_snapshots(ts);

CREATE TABLE ads (
    ad_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT,
    placement TEXT,
    target_type TEXT,
    target_json TEXT,
    cpm REAL NOT NULL,
    button TEXT,
    status TEXT,
    created_date INTEGER,
    hypothesis_id TEXT,
    proposal_id TEXT,
    fingerprint TEXT,
    last_synced_ts INTEGER NOT NULL,
    -- Extra columns: exact API payload so the DB can reproduce getAdsList.
    action_type TEXT,
    promote_url TEXT,
    raw_json TEXT NOT NULL
);

CREATE TABLE ad_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    status TEXT,
    is_paused INTEGER,
    remaining_budget REAL,
    spent_budget REAL,
    views INTEGER,
    opens INTEGER,
    clicks INTEGER,
    actions INTEGER,
    decline_reason_json TEXT
);
CREATE INDEX ad_snapshots_ad_ts ON ad_snapshots(ad_id, ts);

CREATE TABLE ad_stats (
    ad_id INTEGER NOT NULL,
    from_time INTEGER NOT NULL,
    interval INTEGER NOT NULL,
    to_time INTEGER,
    views INTEGER NOT NULL,
    opens INTEGER,
    clicks INTEGER NOT NULL,
    actions INTEGER NOT NULL,
    spent_budget REAL NOT NULL,
    UNIQUE(ad_id, from_time, interval)
);

CREATE TABLE ad_factors (
    ad_id INTEGER NOT NULL,
    factor TEXT NOT NULL,
    level TEXT NOT NULL,
    UNIQUE(ad_id, factor)
);

CREATE TABLE hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    factors_json TEXT NOT NULL,
    stage TEXT,
    status TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);

CREATE TABLE proposals (
    proposal_id TEXT PRIMARY KEY,
    created_ts INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL,
    status TEXT NOT NULL,
    kind TEXT NOT NULL,
    hypothesis_id TEXT,
    stage TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    reserved_amount REAL NOT NULL DEFAULT 0,
    fingerprint TEXT,
    decided_by INTEGER,
    decided_ts INTEGER,
    reject_reason TEXT,
    created_ad_id INTEGER,
    error TEXT
);
CREATE INDEX proposals_status ON proposals(status);
CREATE INDEX proposals_fingerprint ON proposals(fingerprint);

CREATE TABLE proposal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    event TEXT NOT NULL,
    details_json TEXT
);
CREATE INDEX proposal_events_pid ON proposal_events(proposal_id);

CREATE TABLE external_placements (
    channel_username TEXT NOT NULL,
    date TEXT NOT NULL,
    price_usd REAL NOT NULL,
    joins INTEGER NOT NULL,
    views INTEGER,
    UNIQUE(channel_username, date)
);

CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL
);
CREATE INDEX llm_calls_ts ON llm_calls(ts);
