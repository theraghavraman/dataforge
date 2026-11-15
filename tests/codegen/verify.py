#!/usr/bin/env python3
"""Step 2 of the generated-code check: run the code DataForge generated (see export.mjs) and compare every
target with the file the app itself produced for the same sample.

    python tests/codegen/verify.py <out-dir>

  DuckDB SQL   executed in native DuckDB.
  PySpark      executed on a local Spark session.
  Snowflake    no Snowflake account is used. The script must parse as Snowflake SQL, every column must resolve
               under Snowflake's identifier rules (unquoted -> UPPERCASE, quoted -> exact), and its logic is
               executed on DuckDB through a sqlglot translation.
  dbt          `dbt compile` (Jinja, refs, sources, tests), then the same three Snowflake checks on the compiled models.

The Snowflake and dbt checks prove the logic and the name resolution, not Snowflake-specific runtime behaviour.
"""
import glob, json, os, re, shutil, subprocess, sys

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

SAMPLES = ['original', 'phase2', 'phase3']
failures = []


def report(ok, label, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f'  · {detail}' if detail else ''))
    if not ok:
        failures.append(label)


def norm(v):
    if v is None:
        return None
    try:
        return ('%.6f' % float(str(v))).rstrip('0').rstrip('.')
    except ValueError:
        return str(v)


def rows_of(sql, con=None):
    cur = (con or duckdb.connect()).execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


def same_as_app(label, app_csv, cols, rows):
    """Order-insensitive comparison of a result with the app's own target file (column names are case-insensitive)."""
    acols, arows = rows_of(f"select * from read_csv('{app_csv}', header=true, all_varchar=true)")
    if sorted(c.lower() for c in acols) != sorted(c.lower() for c in cols):
        return report(False, label, f'columns differ: app {acols}, generated {cols}')
    idx = [[c.lower() for c in cols].index(c.lower()) for c in acols]
    a = sorted(str(tuple(norm(v) for v in r)) for r in arows)
    b = sorted(str(tuple(norm(r[i]) for i in idx)) for r in rows)
    report(a == b, label, f'{len(rows)} rows match the app' if a == b else f'app {len(arows)} rows, generated {len(rows)}; first difference: {next((x for x in a if x not in b), None)}')


def targets(d):
    """[(node name, node, app output csv)] for every target the app wrote a file for."""
    mapping = json.load(open(f'{d}/mapping.json'))
    by_name = {n['name']: n for n in mapping['nodes']}
    return mapping, [(f[:-4], by_name[f[:-4]], f'{d}/app_out/{f}') for f in sorted(os.listdir(f'{d}/app_out'))]


def sql_name(name):
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


# ---------- DuckDB ----------
def check_duckdb(d):
    _, tgts = targets(d)
    sections = re.split(r'(?m)^-- (.+)\n', open(f'{d}/duckdb.sql').read())
    scripts = {sections[i].strip(): sections[i + 1] for i in range(1, len(sections), 2)}
    con = duckdb.connect()
    cwd = os.getcwd()
    os.chdir(f'{d}/data')
    try:
        for node, _, app_csv in tgts:
            try:
                result = None
                for st in duckdb.extract_statements(scripts[node]):
                    result = con.execute(st)
                same_as_app(f'DuckDB SQL · {node}', app_csv, [c[0] for c in result.description], result.fetchall())
            except Exception as e:
                report(False, f'DuckDB SQL · {node}', str(e).splitlines()[0][:200])
    finally:
        os.chdir(cwd)


# ---------- PySpark ----------
def check_pyspark(d):
    _, tgts = targets(d)
    out = f'{d}/spark_out'
    shutil.rmtree(out, ignore_errors=True)
    run = subprocess.run([sys.executable, 'etl_job.py', '--input-dir', 'data', '--output-dir', 'spark_out'], cwd=d, capture_output=True, text=True)
    if run.returncode:
        return report(False, 'PySpark · script runs', (run.stderr.strip().splitlines() or ['no output'])[-1][:300])
    for node, n, app_csv in tgts:
        folder = re.sub(r'\.(csv|json)$', '', re.sub(r'[^A-Za-z0-9_.-]', '_', n['config'].get('fileName') or node + '.csv')) + '.parquet'
        try:
            cols, rows = rows_of(f"select * from read_parquet('{out}/{folder}/*.parquet')")
            same_as_app(f'PySpark · {node}', app_csv, cols, rows)
        except Exception as e:
            report(False, f'PySpark · {node}', str(e).splitlines()[0][:200])


# ---------- Snowflake ----------
def unquoted_upper(identifier):
    return identifier.name if identifier.args.get('quoted') else identifier.name.upper()


def snowflake_resolves(label, statements):
    """Walk the script's queries in order, tracking each table's columns, and resolve every column reference."""
    schema, unresolved, checked = {}, [], 0
    for st in statements:
        target = query = None
        if isinstance(st, exp.Create) and st.args.get('kind') == 'TABLE' and isinstance(st.expression, exp.Query):
            table = st.this.this if isinstance(st.this, exp.Schema) else st.this
            target, query = unquoted_upper(table.this), st.expression
        elif isinstance(st, exp.Query):
            query = st
        elif isinstance(st, exp.Insert) and isinstance(st.expression, exp.Query):
            query = st.expression
        if query is None:
            continue
        text = query.sql(dialect='snowflake')
        if '@' in text and '$1' in text:      # read from a stage: the columns are the SELECT aliases
            schema[target] = {unquoted_upper(e.args['alias']): 'VARCHAR' for e in query.selects}
            continue
        if '.NEXTVAL' in text.upper():        # sequences are not tables; sqlglot cannot resolve seq.NEXTVAL
            schema[target] = {unquoted_upper(e.args['alias']) if e.args.get('alias') else e.name.upper(): 'VARCHAR' for e in query.selects}
            continue
        checked += 1
        try:
            q = qualify(query.copy(), schema=dict(schema), dialect='snowflake', validate_qualify_columns=True, quote_identifiers=False)
            if target:
                schema[target] = {c: 'VARCHAR' for c in q.named_selects}
        except Exception as e:
            unresolved.append(f'{target or "query"}: {str(e).splitlines()[0][:160]}')
            if target:
                schema[target] = {}
    report(not unresolved, f'{label} · columns resolve under Snowflake identifier rules', f'{checked} queries' if not unresolved else unresolved[0])


def to_duckdb(sql):
    sql = re.sub(r"TO_(?:VAR)?CHAR\((\w+), 'YYYY-MM'\)", r"STRFTIME(\1, '%Y-%m')", sql)   # sqlglot drops the format argument
    return sqlglot.transpile(sql, read='snowflake', write='duckdb')[0]


def check_snowflake(d):
    _, tgts = targets(d)
    text = open(f'{d}/snowflake.sql').read()
    try:
        statements = [s for s in sqlglot.parse(text, read='snowflake', error_level=sqlglot.ErrorLevel.RAISE) if s is not None]
    except Exception as e:
        return report(False, 'Snowflake SQL · parses', str(e).splitlines()[0][:200])
    report(True, 'Snowflake SQL · parses', f'{len(statements)} statements')
    lowercase = sorted({i.name for s in statements for i in s.find_all(exp.Identifier) if i.args.get('quoted') and i.name != i.name.upper()})
    report(not lowercase, 'Snowflake SQL · no lower-case quoted identifiers', ', '.join(lowercase[:5]))
    snowflake_resolves('Snowflake SQL', statements)

    con, variables, errors = duckdb.connect(), set(), []
    use_vars = lambda s: re.sub(r'\$([a-z_][a-z0-9_]*)', lambda m: f"getvariable('{m.group(1)}')" if m.group(1) in variables else m.group(0), s)
    for st in statements:
        raw = st.sql(dialect='snowflake')
        try:
            if isinstance(st, exp.Set):
                name, value = re.match(r'SET\s+(\w+)\s*=\s*(.*)$', raw, re.S | re.I).groups()
                con.execute(f'SET VARIABLE {name.lower()} = {to_duckdb(use_vars(value))}')
                variables.add(name.lower())
            elif raw.upper().startswith('EXECUTE IMMEDIATE'):
                con.execute(con.execute(f"select getvariable('{raw.split('$')[1].strip().lower()}')").fetchone()[0])
            elif isinstance(st, (exp.Transaction, exp.Commit)) or 'FILE FORMAT' in raw.upper():
                pass
            elif '@DATAFORGE_STAGE/' in raw:   # stage read -> the sample file, positional columns, same casts
                file = re.search(r'@DATAFORGE_STAGE/(\S+)', raw).group(1)
                parts = []
                for i, e in enumerate(st.expression.selects):
                    typ = re.search(r'AS (.*)\)$', e.this.sql(dialect='snowflake')).group(1)
                    parts.append(f"TRY_CAST(c{i} AS {'BIGINT' if typ.startswith('NUMBER') else 'DOUBLE' if typ == 'FLOAT' else typ}) AS \"{e.alias}\"")
                n = len(parts)
                con.execute(f"CREATE OR REPLACE TEMP TABLE {st.this.sql(dialect='duckdb')} AS SELECT {', '.join(parts)} FROM read_csv('{d}/data/{file}', header=false, skip=1, all_varchar=true, nullstr='', names=[{', '.join(repr('c' + str(i)) for i in range(n))}])")
            else:
                out = re.sub(r'("?\w+"?)\.NEXTVAL', lambda m: f"nextval('{m.group(1).strip(chr(34))}')", to_duckdb(use_vars(raw)))
                if out.startswith('MERGE'):   # DuckDB does not accept the target alias on the left of UPDATE SET
                    head, sep, tail = out.partition('UPDATE SET')
                    out = head + sep + re.sub(r'\bd\.("\w+") = ', r'\1 = ', tail)
                con.execute(out)
        except Exception as e:
            errors.append(f'{raw[:60]!r}: {str(e).splitlines()[0][:160]}')
    report(not errors, 'Snowflake SQL · logic runs (DuckDB translation)', errors[0] if errors else '')
    for node, _, app_csv in tgts:
        try:
            cols, rows = rows_of(f'SELECT * FROM "{sql_name(node).upper()}"', con)
            same_as_app(f'Snowflake SQL · {node}', app_csv, cols, rows)
        except Exception as e:
            report(False, f'Snowflake SQL · {node}', str(e).splitlines()[0][:200])


# ---------- dbt ----------
def check_dbt(d):
    mapping, tgts = targets(d)
    project = os.path.abspath(f'{d}/dbt')
    profiles = os.path.abspath(f'{d}/dbt_profiles')
    os.makedirs(profiles, exist_ok=True)
    open(f'{profiles}/profiles.yml', 'w').write(f"dataforge:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: {profiles}/dev.duckdb\n")
    local = os.environ.get('DBT_UTILS_LOCAL')   # offline runs: a local checkout of dbt-labs/dbt-utils
    if local and os.path.exists(f'{project}/packages.yml'):
        open(f'{project}/packages.yml', 'w').write(f'packages:\n  - local: {os.path.abspath(local)}\n')
    for step in (['deps'] if os.path.exists(f'{project}/packages.yml') else []) + ['compile']:
        run = subprocess.run(['dbt', step, '--profiles-dir', profiles], cwd=project, capture_output=True, text=True)
        if run.returncode:
            return report(False, f'dbt · dbt {step}', ' '.join((run.stdout + run.stderr).strip().splitlines()[-3:])[:300])
    compiled = glob.glob(f'{project}/target/compiled/*/models')[0]
    report(True, 'dbt · project compiles', f"{len(glob.glob(compiled + '/**/*.sql', recursive=True))} models")

    files = [f for f in glob.glob(f'{project}/target/compiled/**/*.sql', recursive=True) if os.path.isfile(f)]
    bad = []
    for f in files:
        try:
            sqlglot.parse(open(f).read(), read='snowflake', error_level=sqlglot.ErrorLevel.RAISE)
        except Exception as e:
            bad.append(f'{os.path.basename(f)}: {str(e).splitlines()[0][:120]}')
    report(not bad, 'dbt · compiled models and tests parse as Snowflake SQL', bad[0] if bad else f'{len(files)} files')

    # Source tables as a Snowflake load would create them: upper-case column names.
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS dev; USE dev; CREATE SCHEMA dev.\"RAW\"")
    schema = {'DEV': {'RAW': {}, 'MAIN': {}}}
    for n in mapping['nodes']:
        if n['type'].endswith('_source'):
            path = f"{d}/data/{n['config']['fileName']}"
            cols = [c[0] for c in con.execute(f"select * from read_csv('{path}', header=true, all_varchar=true) limit 0").description]
            con.execute(f"CREATE TABLE dev.\"RAW\".\"{sql_name(n['name']).upper()}\" AS SELECT {', '.join(f'\"{c}\" AS \"{c.upper()}\"' for c in cols)} FROM read_csv('{path}', header=true, all_varchar=true, nullstr='')")
            schema['DEV']['RAW'][sql_name(n['name']).upper()] = {c.upper(): 'VARCHAR' for c in cols}
    unresolved, errors = [], []
    for layer, kind in (('staging', 'VIEW'), ('marts', 'TABLE')):
        for f in sorted(glob.glob(f'{compiled}/{layer}/*.sql')):
            name, sql = os.path.basename(f)[:-4], open(f).read()
            try:   # dbt-duckdb renders "dev"."main"."x"; Snowflake resolves the unquoted, upper-case relation
                as_snowflake = re.sub(r'"dev"\."(RAW|main)"\."(\w+)"', lambda m: f'DEV.{m.group(1).upper()}.{m.group(2).upper()}', sql)
                q = qualify(sqlglot.parse_one(as_snowflake, read='snowflake'), schema=schema, dialect='snowflake', validate_qualify_columns=True, quote_identifiers=False)
                schema['DEV']['MAIN'][name.upper()] = {c: 'VARCHAR' for c in q.named_selects}
            except Exception as e:
                unresolved.append(f'{name}: {str(e).splitlines()[0][:160]}')
            try:
                con.execute(f'CREATE {kind} dev.main."{name}" AS {to_duckdb(sql)}')
            except Exception as e:
                errors.append(f'{name}: {str(e).splitlines()[0][:160]}')
    report(not unresolved, 'dbt · columns resolve under Snowflake identifier rules', unresolved[0] if unresolved else '')
    report(not errors, 'dbt · models run (DuckDB translation)', errors[0] if errors else '')
    for node, n, app_csv in tgts:
        try:
            cols, rows = rows_of(f'SELECT * FROM dev.main."mart_{sql_name(node).lower()}_{n["id"]}"', con)
            same_as_app(f'dbt · {node}', app_csv, cols, rows)
        except Exception as e:
            report(False, f'dbt · {node}', str(e).splitlines()[0][:200])


if __name__ == '__main__':
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'codegen-out')
    for sample in SAMPLES:
        print(f'\n{sample}')
        d = os.path.join(root, sample)
        for check in (check_duckdb, check_pyspark, check_snowflake, check_dbt):
            check(d)
    print(f"\n{'All generated code checks passed' if not failures else str(len(failures)) + ' generated-code check(s) failed'}")
    sys.exit(1 if failures else 0)
