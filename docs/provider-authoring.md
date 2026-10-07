# Warehouse provider authoring

A provider implements `WarehouseProvider` from `src/dbt_agents/providers/base.py`:

- `capabilities`
- `describe_relation`
- `dry_run`
- `execute_read`
- `cancel`

It must preserve these semantics:

1. accept only one provider-native read statement;
2. validate every referenced catalog/schema against configuration;
3. estimate work before execution and enforce a server-side budget;
4. execute with a principal that cannot persist warehouse changes;
5. bound time, rows, cells, and serialized result size;
6. return a stable job/query ID and cost evidence; and
7. support cancellation or explicitly report that cancellation is unavailable.

Adding a provider also requires parser-bypass fixtures and integration tests that
prove the query identity cannot write. A provider must not claim support merely
because its SDK can execute SQL.
