CREATE TABLE IF NOT EXISTS product_work_snapshot_history(
    user_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, product_id, version),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS product_work_snapshot_history_product
    ON product_work_snapshot_history(user_id, product_id, version);
