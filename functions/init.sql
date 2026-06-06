
CREATE TABLE IF NOT EXISTS averaged_data (
    id SERIAL PRIMARY KEY,
    interval_start TIMESTAMP NOT NULL,
    average_value DOUBLE PRECISION NOT NULL,
    count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_interval_start ON averaged_data(interval_start);
