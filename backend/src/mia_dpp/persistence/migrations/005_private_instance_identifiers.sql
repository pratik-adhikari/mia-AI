CREATE TABLE IF NOT EXISTS product_instance_identifiers(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, product_id, id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS product_instance_identifiers_owner
    ON product_instance_identifiers(user_id, product_id, created_at);
