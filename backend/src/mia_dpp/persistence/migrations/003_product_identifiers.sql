CREATE TABLE IF NOT EXISTS product_identifiers(
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    scheme TEXT NOT NULL,
    namespace TEXT,
    normalized_value TEXT NOT NULL,
    role TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, scheme, namespace, normalized_value, role),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS product_identifiers_product
    ON product_identifiers(product_id, created_at);
CREATE INDEX IF NOT EXISTS product_identifiers_lookup
    ON product_identifiers(scheme, namespace, normalized_value, role);
CREATE UNIQUE INDEX IF NOT EXISTS product_identity_unique
    ON product_identifiers(scheme, COALESCE(namespace, ''), normalized_value)
    WHERE role='identity';
