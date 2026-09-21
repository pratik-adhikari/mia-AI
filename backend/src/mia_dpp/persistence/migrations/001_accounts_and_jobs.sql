CREATE TABLE IF NOT EXISTS threads(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_user_updated ON threads(user_id, updated_at);

CREATE TABLE IF NOT EXISTS user_products(
    user_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, product_id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS user_products_user ON user_products(user_id, created_at);

CREATE TABLE IF NOT EXISTS background_jobs(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, job_type),
    FOREIGN KEY(thread_id) REFERENCES threads(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS background_jobs_thread_status
    ON background_jobs(thread_id, status, updated_at);
CREATE INDEX IF NOT EXISTS background_jobs_user_updated
    ON background_jobs(user_id, updated_at);
