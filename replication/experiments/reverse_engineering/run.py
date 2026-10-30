"""Run deterministic source-to-class baselines on prepared corpus snapshots.

    python -m experiments.reverse_engineering.run --projects shopping-cart

Failures are written to each reference_manifest.json; no empty diagram stands
in for a failed tool. Installations are explicit, never triggered by this runner.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from app.evaluation.puml_parser import parse_puml
from experiments.reverse_engineering.facts import ClassFacts, Facts, read_puml, render_puml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
EXTENSIONS = {"java": {".java"}, "python": {".py"}, "php": {".php"},
              "javascript": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding="utf-8")


def executable(name: str, local: Path | None = None) -> str:
    if local and local.is_file():
        return str(local.resolve())
    if found := shutil.which(name):
        return found
    if name == "java" and Path('/opt/homebrew/opt/openjdk/bin/java').is_file():
        return '/opt/homebrew/opt/openjdk/bin/java'
    raise FileNotFoundError(f"Missing {name}; see experiments/reverse_engineering/README.md")


def java_executable() -> str:
    if home := os.environ.get('JAVA_HOME'):
        return str(Path(home) / 'bin/java')
    # macOS /usr/bin/java can be a nonfunctional launcher, not a runtime.
    brew = Path('/opt/homebrew/opt/openjdk/bin/java')
    return str(brew) if brew.exists() else executable('java')


def resolve_java_type(raw: str, owner: dict, names: set[str]) -> str | None:
    raw = re.sub(r'<.*>', '', raw).replace('[]', '').strip()
    if raw in names:
        return raw
    # Nested owners first; then explicit imports and same package.
    scope = owner['name'].rpartition('.')[0]
    while scope:
        if f'{scope}.{raw}' in names:
            return f'{scope}.{raw}'
        scope = scope.rpartition('.')[0]
    for imported in owner['imports']:
        candidate = imported[:-1] + raw if imported.endswith('.*') else imported
        if candidate.rsplit('.', 1)[-1] == raw and candidate in names:
            return candidate
    # No global short-name guessing: javac attribution was deliberately not run.
    return None


def javac_facts(data: dict) -> Facts:
    facts = Facts(warnings=data.get('failures', []))
    names = {c['name'] for c in data['classes']}
    for c in data['classes']:
        facts.classes.append(ClassFacts(c['name'], c['kind'], sorted(set(c['attributes'])), sorted(set(c['methods']))))
        for parent in c['parents']:
            if resolved := resolve_java_type(parent, c, names):
                facts.inheritance.append((c['name'], resolved))
        for raw in c['field_types']:
            for token in re.findall(r'[\w.]+', raw):
                resolved = resolve_java_type(token, c, names)
                if resolved and resolved != c['name']:
                    facts.associations.append((c['name'], resolved))
    facts.inheritance = sorted(set(facts.inheritance))
    facts.associations = sorted(set(facts.associations))
    return facts


def run_project(project: dict, args: argparse.Namespace) -> dict:
    pid, language = project['id'], project['language']
    project_dir = args.data_root / pid
    snapshot = project_dir / 'snapshot'
    reference = project_dir / 'reference'
    reference.mkdir(parents=True, exist_ok=True)
    out = reference / 'class'
    # Archive previous artifacts so a failed rerun cannot be evaluated as success.
    if out.exists():
        archive = reference / '_previous_runs' / str(time.time_ns())
        archive.parent.mkdir(exist_ok=True)
        out.rename(archive)
    old_facts = reference / 'reference_facts.json'
    if old_facts.exists():
        old_facts.unlink()
    manifest = {'schema_version': 1, 'project': pid, 'language': language,
                'status': 'error', 'tool': {'python': 'pyreverse', 'java': 'javac-ast-adapter',
                                          'php': 'ph-puml', 'javascript': 'tplant'}[language],
                'version': None, 'commands': [], 'runtime_seconds': None,
                'classes': None, 'relations': None, 'failures': [], 'output_files': [],
                'scope': 'entire prepared snapshot, including tests; internal inheritance only',
                'llm_calls': 0, 'source_snapshot': str(snapshot), 'python_version': sys.version,
                'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'facts_reader_sha256': hashlib.sha256((PACKAGE/'facts.py').read_bytes()).hexdigest(),
                'evaluation_parser_sha256': hashlib.sha256((ROOT/'app/evaluation/puml_parser.py').read_bytes()).hexdigest()}
    resolved_path = args.corpus.parent / 'corpus_resolved.json'
    resolved = json.loads(resolved_path.read_text()) if resolved_path.exists() else {}
    manifest['source_commit'] = resolved.get(pid, {}).get('commit')
    started = time.perf_counter()
    environment = {**os.environ, 'PYTHONHASHSEED': '0'}

    def command(cmd: list[str], cwd: Path, **kwargs) -> str:
        record = {'argv': cmd, 'shell_command': shlex.join(cmd), 'cwd': str(cwd)}
        manifest['commands'].append(record)
        t0 = time.perf_counter()
        try:
            result = subprocess.run(cmd, cwd=cwd, env=environment, text=True,
                                    capture_output=True, timeout=args.timeout, **kwargs)
            record.update(returncode=result.returncode, runtime_seconds=round(time.perf_counter()-t0, 6),
                          stdout=result.stdout, stderr=result.stderr)
            if result.returncode:
                raise RuntimeError(f"{cmd[0]} exited {result.returncode}: {result.stderr[-1500:]}")
            return result.stdout
        finally:
            record.setdefault('runtime_seconds', round(time.perf_counter()-t0, 6))

    try:
        if not snapshot.is_dir():
            raise FileNotFoundError(f"Missing snapshot; run prepare --projects {pid}")
        files = sorted(p for p in snapshot.rglob('*') if p.is_file() and p.suffix in EXTENSIONS[language])
        if not files:
            raise ValueError('Snapshot contains no source files for this language')
        source_hash = hashlib.sha256()
        for path in files:
            source_hash.update(path.relative_to(snapshot).as_posix().encode() + b'\0')
            source_hash.update(hashlib.sha256(path.read_bytes()).digest())
        manifest.update(source_files=len(files), source_sha256=source_hash.hexdigest())
        with tempfile.TemporaryDirectory(prefix='re-', dir=reference) as temp:
            work = Path(temp)
            if language == 'java':
                java = args.java or java_executable()
                version = command([java, '-version'], work)
                # java -version writes stderr on OpenJDK.
                manifest['version'] = (version or manifest['commands'][-1]['stderr']).strip()
                manifest['tool_limitation'] = 'Custom serialization over OpenJDK javac parse API; not an established RE product; no symbol attribution.'
                manifest['java_adapter_sha256'] = hashlib.sha256((PACKAGE/'JavacReference.java').read_bytes()).hexdigest()
                raw = command([java, str(PACKAGE/'JavacReference.java'), str(snapshot)], work)
                data = json.loads(raw)
                write_json(reference/'javac_ast.json', data)
                if data['failures']:
                    raise ValueError(f"javac reported {len(data['failures'])} syntax errors; see javac_ast.json")
                facts = javac_facts(data)
                (work/'classes.puml').write_text(render_puml(facts))
                manifest['declared_parent_clauses'] = sum(len(c['parents']) for c in data['classes'])
                manifest['unresolved_or_external_parent_clauses'] = manifest['declared_parent_clauses'] - len(facts.inheritance)
            elif language == 'python':
                manifest['version'] = importlib.metadata.version('pylint')
                home_config = Path.home()/'.pyreverserc'
                manifest['pyreverse_home_config_sha256'] = hashlib.sha256(home_config.read_bytes()).hexdigest() if home_config.exists() else None
                manifest['dependencies'] = {p: importlib.metadata.version(p) for p in
                    ['pylint', 'astroid', 'dill', 'isort', 'mccabe', 'platformdirs', 'tomlkit']}
                # Explicit files include non-package directories that pyreverse
                # would omit when given only a repository directory.
                inputs = work/'python_inputs.txt'
                inputs.write_text('\n'.join(str(p) for p in files) + '\n')
                command([executable('pyreverse', Path(sys.executable).parent/'pyreverse'),
                         '-o', 'puml', '-f', 'ALL', '-m', 'y', '-p', 'reference',
                         '--ignore=', *map(str, files)], work)
                facts = read_puml((work/'classes_reference.puml').read_text())
            elif language == 'javascript':
                package_file = PACKAGE/'node_modules/tplant/package.json'
                if not package_file.exists():
                    raise FileNotFoundError('tplant is not installed locally; run npm install in the package directory')
                manifest['version'] = json.loads(package_file.read_text())['version']
                manifest['node_version'] = command([executable('node'), '--version'], work).strip()
                # allowJs lets TypeScript parse ES classes. Dynamic/prototype
                # construction remains outside tplant's declared-class model.
                config = work/'tsconfig.json'
                write_json(config, {'compilerOptions': {'allowJs': True, 'checkJs': False,
                    'noEmit': True, 'skipLibCheck': True, 'target': 'ESNext'},
                    'files': [str(p) for p in files]})
                command([str(PACKAGE/'node_modules/.bin/tplant'), '--project', str(config),
                         '--associations', '--output', str(work/'classes.puml')], work)
                facts = read_puml((work/'classes.puml').read_text())
                manifest['tool_limitation'] = 'Plain JS via allowJs is experimental until the installed tplant passes the JS integration fixture; dynamic/prototype classes not covered.'
            else:
                php = executable('php')
                tool = Path(args.ph_puml).resolve()
                if not tool.exists():
                    raise FileNotFoundError(f'Missing ph-puml: {tool}')
                manifest['php_version'] = command([php, '--version'], work).strip()
                # --version is not a reliable package version for this Symfony
                # application: require the installed source commit or lock entry.
                commit = command(['git', '-C', str(tool.parent.parent), 'rev-parse', 'HEAD'], work).strip()
                manifest['version'] = commit
                lock = tool.parent.parent/'composer.lock'
                if lock.exists():
                    manifest['composer_lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
                    manifest['dependencies'] = {p['name']: p['version'] for p in json.loads(lock.read_text())['packages']}
                command([php, str(tool), str(snapshot), str(work/'classes.puml')], work)
                facts = read_puml((work/'classes.puml').read_text())
            if not facts.classes:
                raise ValueError('Tool emitted zero classes; no usable class reference (this is not perfect recall)')
            outputs = sorted(p for p in work.glob('*.puml') if p.name.startswith('classes'))
            compatibility = []
            for puml in outputs:
                parsed = parse_puml(puml.read_text(), 'class')
                compatibility.append({'file': puml.name, 'elements': parsed.element_count,
                    'relationships': parsed.relationship_count, 'warnings': parsed.parse_warnings})
            expected_relations = len(facts.inheritance) + len(facts.associations)
            if sum(p['relationships'] for p in compatibility) != expected_relations:
                raise ValueError('Evaluator relationship count differs from reference facts')
            # pyreverse/ph-puml hide external nodes by default; Java does likewise.
            if sum(p['elements'] for p in compatibility) != len(facts.classes):
                raise ValueError('Evaluator element count differs from reference classes; inspect implicit/external nodes')
            out.mkdir()
            for puml in outputs:
                shutil.copy2(puml, out/puml.name)
            write_json(reference/'reference_facts.json', facts.to_dict())
            manifest.update(status='ok', classes=len(facts.classes), relations=expected_relations,
                inheritance=len(facts.inheritance), attributes=sum(len(c.attributes) for c in facts.classes),
                methods=sum(len(c.methods) for c in facts.classes),
                output_files=[str((out/p.name).relative_to(project_dir)) for p in outputs],
                output_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs},
                facts_sha256=hashlib.sha256((reference/'reference_facts.json').read_bytes()).hexdigest(),
                parser_compatibility=compatibility)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, importlib.metadata.PackageNotFoundError) as exc:
        manifest['failures'].append(f'{type(exc).__name__}: {exc}')
    manifest['runtime_seconds'] = round(time.perf_counter()-started, 6)
    write_json(reference/'reference_manifest.json', manifest)
    print(f"{pid}: {manifest['status']} ({manifest['classes']} classes, {manifest['relations']} relations)")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT/'experiments/corpus.json')
    parser.add_argument('--data-root', type=Path, default=ROOT/'data')
    parser.add_argument('--projects', nargs='+')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--java')
    parser.add_argument('--ph-puml', default=str(PACKAGE/'tools/ph-puml/bin/ph-puml'))
    args = parser.parse_args()
    args.data_root = args.data_root.resolve()
    projects = json.loads(args.corpus.read_text())['projects']
    if args.projects:
        unknown = set(args.projects) - {p['id'] for p in projects}
        if unknown:
            parser.error(f'Unknown project IDs: {sorted(unknown)}')
    results = [run_project(p, args) for p in projects if not args.projects or p['id'] in args.projects]
    if any(r['status'] != 'ok' for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
