CREATE TABLE IF NOT EXISTS product_work_snapshots(
    user_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, product_id)
);
CREATE INDEX IF NOT EXISTS product_work_snapshots_updated
    ON product_work_snapshots(user_id, updated_at);

CREATE TABLE IF NOT EXISTS human_reviews(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS human_reviews_product
    ON human_reviews(user_id, product_id, created_at);
CREATE INDEX IF NOT EXISTS human_reviews_run
    ON human_reviews(run_id, created_at);
