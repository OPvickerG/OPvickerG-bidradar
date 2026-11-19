-- PostgreSQL DDL rendered from the Alembic migrations (alembic upgrade head --sql). Apply migrations with
-- `alembic upgrade head` rather than running this file by hand; it is kept for review and disaster recovery.

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

CREATE TABLE app_settings (
    key VARCHAR(64) NOT NULL, 
    value JSON NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (key)
);

CREATE TABLE categories (
    id SERIAL NOT NULL, 
    code VARCHAR(8) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    description TEXT, 
    enabled BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (code)
);

CREATE TABLE scan_executions (
    id SERIAL NOT NULL, 
    slot_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    trigger VARCHAR(16) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    stats JSON NOT NULL, 
    error TEXT, 
    worker_id VARCHAR(128), 
    PRIMARY KEY (id), 
    CONSTRAINT uq_scan_slot_trigger UNIQUE (slot_at, trigger)
);

CREATE TABLE scheduler_locks (
    name VARCHAR(64) NOT NULL, 
    holder VARCHAR(128), 
    acquired_at TIMESTAMP WITH TIME ZONE, 
    expires_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (name)
);

CREATE TABLE sources (
    id SERIAL NOT NULL, 
    key VARCHAR(64) NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    base_url VARCHAR(500) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    notes TEXT, 
    last_success_at TIMESTAMP WITH TIME ZONE, 
    last_error_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    consecutive_failures INTEGER NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (key)
);

CREATE TABLE suppressed_notifications (
    id SERIAL NOT NULL, 
    dedupe_key VARCHAR(200) NOT NULL, 
    reason VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE system_events (
    id SERIAL NOT NULL, 
    level VARCHAR(16) NOT NULL, 
    event VARCHAR(64) NOT NULL, 
    message TEXT NOT NULL, 
    data JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE tenders (
    id SERIAL NOT NULL, 
    source_key VARCHAR(64) NOT NULL, 
    external_id VARCHAR(128) NOT NULL, 
    bid_number VARCHAR(128), 
    ra_number VARCHAR(128), 
    parent_bid_number VARCHAR(128), 
    title TEXT NOT NULL, 
    description TEXT, 
    procurement_type VARCHAR(64), 
    organization VARCHAR(300), 
    department VARCHAR(300), 
    ministry VARCHAR(300), 
    location VARCHAR(300), 
    state VARCHAR(100), 
    published_at TIMESTAMP WITH TIME ZONE, 
    source_updated_at TIMESTAMP WITH TIME ZONE, 
    deadline_at TIMESTAMP WITH TIME ZONE, 
    opening_at TIMESTAMP WITH TIME ZONE, 
    status VARCHAR(32) NOT NULL, 
    stage VARCHAR(64), 
    quantity FLOAT, 
    estimated_value NUMERIC(18, 2), 
    emd NUMERIC(18, 2), 
    performance_security VARCHAR(100), 
    contract_duration VARCHAR(100), 
    details JSON NOT NULL, 
    relevance_score INTEGER NOT NULL, 
    best_category VARCHAR(8), 
    match_explanation TEXT, 
    is_relevant BOOLEAN NOT NULL, 
    watched BOOLEAN NOT NULL, 
    fingerprint VARCHAR(64), 
    official_url VARCHAR(600), 
    document_url VARCHAR(600), 
    first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_verified_at TIMESTAMP WITH TIME ZONE, 
    last_refreshed_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_tender_source_external UNIQUE (source_key, external_id)
);

CREATE INDEX ix_tender_deadline ON tenders (deadline_at);

CREATE INDEX ix_tender_score ON tenders (relevance_score);

CREATE INDEX ix_tender_status ON tenders (status);

CREATE TABLE bid_observations (
    id SERIAL NOT NULL, 
    tender_id INTEGER NOT NULL, 
    scan_id INTEGER, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    fingerprint VARCHAR(64) NOT NULL, 
    state JSON NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(scan_id) REFERENCES scan_executions (id) ON DELETE SET NULL, 
    FOREIGN KEY(tender_id) REFERENCES tenders (id) ON DELETE CASCADE, 
    CONSTRAINT uq_observation UNIQUE (tender_id, scan_id)
);

CREATE TABLE email_notifications (
    id SERIAL NOT NULL, 
    dedupe_key VARCHAR(200) NOT NULL, 
    tender_id INTEGER, 
    event_type VARCHAR(32) NOT NULL, 
    subject VARCHAR(400) NOT NULL, 
    body_text TEXT NOT NULL, 
    body_html TEXT, 
    recipient VARCHAR(320), 
    status VARCHAR(32) NOT NULL, 
    provider VARCHAR(32), 
    provider_message_id VARCHAR(200), 
    attempts INTEGER NOT NULL, 
    next_attempt_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    delivered_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tender_id) REFERENCES tenders (id) ON DELETE SET NULL, 
    CONSTRAINT uq_notification_dedupe UNIQUE (dedupe_key)
);

CREATE INDEX ix_notif_status ON email_notifications (status);

CREATE TABLE keywords (
    id SERIAL NOT NULL, 
    category_id INTEGER NOT NULL, 
    phrase VARCHAR(200) NOT NULL, 
    kind VARCHAR(16) NOT NULL, 
    weight FLOAT NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    user_defined BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(category_id) REFERENCES categories (id) ON DELETE CASCADE, 
    CONSTRAINT uq_keyword UNIQUE (category_id, phrase, kind)
);

CREATE TABLE scan_errors (
    id SERIAL NOT NULL, 
    scan_id INTEGER, 
    source_key VARCHAR(64), 
    stage VARCHAR(48) NOT NULL, 
    message TEXT NOT NULL, 
    detail TEXT, 
    tender_ref VARCHAR(128), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(scan_id) REFERENCES scan_executions (id) ON DELETE CASCADE
);

CREATE TABLE tender_documents (
    id SERIAL NOT NULL, 
    tender_id INTEGER NOT NULL, 
    kind VARCHAR(32) NOT NULL, 
    url VARCHAR(600) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    sha256 VARCHAR(64), 
    pages INTEGER, 
    text_excerpt TEXT, 
    extracted_fields JSON NOT NULL, 
    error TEXT, 
    fetched_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tender_id) REFERENCES tenders (id) ON DELETE CASCADE, 
    CONSTRAINT uq_tender_document UNIQUE (tender_id, url)
);

CREATE TABLE tender_matches (
    id SERIAL NOT NULL, 
    tender_id INTEGER NOT NULL, 
    category_code VARCHAR(8) NOT NULL, 
    score INTEGER NOT NULL, 
    matched_phrases JSON NOT NULL, 
    explanation TEXT NOT NULL, 
    breakdown JSON NOT NULL, 
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tender_id) REFERENCES tenders (id) ON DELETE CASCADE, 
    CONSTRAINT uq_match UNIQUE (tender_id, category_code)
);

CREATE TABLE tender_status_history (
    id SERIAL NOT NULL, 
    tender_id INTEGER NOT NULL, 
    scan_id INTEGER, 
    event_type VARCHAR(48) NOT NULL, 
    field VARCHAR(64), 
    old_value TEXT, 
    new_value TEXT, 
    significant BOOLEAN NOT NULL, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(scan_id) REFERENCES scan_executions (id) ON DELETE SET NULL, 
    FOREIGN KEY(tender_id) REFERENCES tenders (id) ON DELETE CASCADE
);

CREATE INDEX ix_event_tender ON tender_status_history (tender_id);

CREATE TABLE email_delivery_attempts (
    id SERIAL NOT NULL, 
    notification_id INTEGER NOT NULL, 
    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    provider VARCHAR(32) NOT NULL, 
    outcome VARCHAR(24) NOT NULL, 
    http_status INTEGER, 
    detail TEXT, 
    PRIMARY KEY (id), 
    FOREIGN KEY(notification_id) REFERENCES email_notifications (id) ON DELETE CASCADE
);

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

COMMIT;

