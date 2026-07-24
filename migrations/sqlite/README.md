# SQLite migrations

SQLite migration filenames must match `NNNN_name.sql`, where `NNNN` is a four-digit
version and `name` starts with a lowercase letter and contains only lowercase letters,
digits, and underscores. Migration files must be UTF-8 encoded.

Migrations are forward-only. Their binary SHA-256 checksum is part of the catalog
contract, so a migration must never be edited after it has been applied.

Migration SQL must not contain transaction-control statements: `BEGIN`, `COMMIT`,
`ROLLBACK`, `SAVEPOINT`, or `RELEASE`. The migration runner owns transactions.

This phase intentionally contains no production `.sql` files.
