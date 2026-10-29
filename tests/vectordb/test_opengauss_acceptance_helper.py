"""Dedicated-container acceptance isolation and credential-safe pytest entrypoint.

Run with the validation Python and pass pytest arguments. Credentials stay in
memory; each consuming fixture gets its own schema and session-only settings.
"""

import json
import os
import subprocess
import sys
import uuid
from urllib.parse import quote, unquote, urlparse

import pytest

_CONTAINER = "ov-retrieval-acceptance-20261016"


def acceptance_target():
    dsn = os.getenv("OPENVIKING_OPENGAUSS_FEATURE_DSN")
    if not dsn:
        pytest.skip("OPENVIKING_OPENGAUSS_FEATURE_DSN is unset; dedicated real DB required")
    parsed = urlparse(dsn)
    if not (
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname == "127.0.0.1"
        and parsed.port == 35436
        and parsed.path == "/postgres"
        and unquote(parsed.username or "") == "gaussdb"
        and not parsed.query
        and not parsed.fragment
    ):
        pytest.fail("only dedicated 127.0.0.1:35436/postgres is permitted", pytrace=False)
    return parsed


@pytest.fixture
def acceptance_isolation(monkeypatch):
    import psycopg2
    import psycopg2.pool

    target = acceptance_target()
    schema = "ov_accept_" + uuid.uuid4().hex[:16]
    admin = psycopg2.connect(
        host=target.hostname, port=target.port, user=unquote(target.username),
        password=unquote(target.password or ""), dbname="postgres", connect_timeout=10,
        options="-c client_encoding=UTF8 -c statement_timeout=60000",
    )
    admin.autocommit = True
    pool_factory = psycopg2.pool.ThreadedConnectionPool
    pools = []
    with admin.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
        cursor.execute(
            f'CREATE TABLE "{schema}"."_ov_collection_meta" '
            '(table_name VARCHAR(256) PRIMARY KEY, meta_json TEXT NOT NULL)'
        )

    def isolated_pool(*args, **kwargs):
        assert not args, "acceptance requires explicit connection keywords"
        assert (kwargs.get("host"), kwargs.get("port"), kwargs.get("dbname")) == (
            "127.0.0.1", 35436, "postgres"
        ), "refusing a non-acceptance connection"
        kwargs["connect_timeout"] = 10
        kwargs["options"] = (
            f"-c client_encoding=UTF8 -c search_path={schema},public "
            "-c statement_timeout=60000"
        )
        pool = pool_factory(**kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", isolated_pool)
    try:
        yield schema
    finally:
        for pool in pools:
            if not pool.closed:
                pool.closeall()
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')
                cursor.execute("SELECT count(*) FROM pg_namespace WHERE nspname=%s", (schema,))
                assert cursor.fetchone()[0] == 0
                print(f"\nOPENGAUSS_ACCEPTANCE_CLEANUP schema={schema} absent=true", flush=True)
        finally:
            admin.close()


def analyze_acceptance(collection, *tables):
    import psycopg2

    target = acceptance_target()
    schema = collection._execute("SELECT current_schema()", fetch=True)[0][0]
    assert schema.startswith("ov_accept_") and schema.isidentifier()
    conn = psycopg2.connect(
        host=target.hostname, port=target.port, user=unquote(target.username),
        password=unquote(target.password or ""), dbname="postgres", connect_timeout=10,
        options=f"-c search_path={schema},public -c statement_timeout=60000",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            for table in tables:
                assert table.isidentifier() and table in {
                    collection._name, collection._sparse_posting_table_name()
                }
                cursor.execute(f'ANALYZE "{table}"')
    finally:
        conn.close()


def verify_acceptance_cleanup(schema, collection):
    import psycopg2

    target = acceptance_target()
    assert schema.startswith("ov_accept_") and schema.isidentifier()
    assert collection.startswith("ov_scale_") and collection.isidentifier()
    conn = psycopg2.connect(
        host=target.hostname, port=target.port, user=unquote(target.username),
        password=unquote(target.password or ""), dbname="postgres", connect_timeout=10,
        options="-c statement_timeout=15000",
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT nspname FROM pg_namespace WHERE nspname=%s", (schema,))
            schemas = cursor.fetchall()
            cursor.execute("SELECT relname FROM pg_class WHERE position(%s in relname)>0", (collection,))
            relations = cursor.fetchall()
            assert not schemas and not relations, (schemas, relations)
            print(f"OPENGAUSS_PRIOR_SCALE_CLEANUP schema={schema} schemas=0 relations=0", flush=True)
        conn.rollback()
    finally:
        conn.close()


def _set_dsn_from_container():
    result = subprocess.run(
        ["docker", "inspect", _CONTAINER], capture_output=True, text=True, check=True, timeout=10
    )
    container = json.loads(result.stdout)[0]
    assert container["Name"] == "/" + _CONTAINER
    assert container["State"]["Running"]
    bindings = container["NetworkSettings"]["Ports"]["5432/tcp"]
    assert {("127.0.0.1", "35436")} == {
        (item["HostIp"], item["HostPort"]) for item in bindings
    }
    env = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
    password = env.get("GS_PASSWORD") or env.get("POSTGRES_PASSWORD")
    assert password, "dedicated container has no supported credential environment variable"
    os.environ["OPENVIKING_OPENGAUSS_FEATURE_DSN"] = (
        "postgresql://gaussdb:" + quote(password, safe="") + "@127.0.0.1:35436/postgres"
    )


if __name__ == "__main__":
    _set_dsn_from_container()
    raise SystemExit(pytest.main(sys.argv[1:]))
