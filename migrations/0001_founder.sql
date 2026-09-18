-- OPEX MONEY Founder Flow
-- PostgreSQL migration

CREATE TABLE IF NOT EXISTS bot_groups (
    group_id BIGINT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    username VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_nations_currency_code
    ON nations(currency_code);

CREATE UNIQUE INDEX IF NOT EXISTS uq_nations_active_group
    ON nations(group_id)
    WHERE is_active = TRUE;
