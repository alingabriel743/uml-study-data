"""Production-source RE references for the corrected archival-cutoff v2 corpus.

Run with revision_work/.venv/bin/python -m experiments.reverse_engineering.run_v2.
No builds, IR regeneration, LLM calls or changes to source checkouts occur.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from app.evaluation.puml_parser import parse_puml
from experiments.reverse_engineering.facts import read_puml, render_puml
from experiments.reverse_engineering.run import (
    EXTENSIONS, PACKAGE, ROOT, java_executable, javac_facts, write_json,
)
from experiments.reverse_engineering.v2_source_discovery import discover_source_files
from experiments.reverse_engineering.native_readers import read_tplant

TOOLS = PACKAGE / 'tools'


def source_files(root: Path, language: str) -> list[Path]:
    return discover_source_files(root, tuple('*' + e for e in sorted(EXTENSIONS[language])))


def signature(root: Path, files: list[Path]) -> str:
    result = hashlib.sha256()
    for path in files:
        result.update(path.relative_to(root).as_posix().encode() + b'\0')
        result.update(hashlib.sha256(path.read_bytes()).digest())
    return result.hexdigest()


def run(project: dict) -> dict:
    pid, language = project['id'], project['language']
    directory = ROOT / 'data' / pid
    source = directory / 'src'
    reference = directory / 'reference'
    reference.mkdir(exist_ok=True)
    output = reference / 'class'
    if output.exists():
        archive = reference / '_previous_runs' / str(time.time_ns())
        archive.parent.mkdir(exist_ok=True)
        output.rename(archive)
    (reference / 'reference_facts.json').unlink(missing_ok=True)
    manifest = dict(schema_version=2, project=pid, language=language, status='error',
                    tool=None, version=None, commands=[], failures=[], output_files=[], llm_calls=0,
                    source_snapshot=str(source), source_directory='src',
                    source_rule='v2-production', scope='exact v2 production-source discovery; direct internal inheritance',
                    source_rule_sha256=hashlib.sha256((PACKAGE/'v2_source_discovery.py').read_bytes()).hexdigest(),
                    adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    evaluation_parser_sha256=hashlib.sha256((ROOT/'app/evaluation/puml_parser.py').read_bytes()).hexdigest())
    environment = {**os.environ, 'PYTHONHASHSEED': '0', 'LC_ALL': 'C.UTF-8'}
    started = time.perf_counter()

    def command(argv: list, cwd: Path = reference, optional: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
        argv = list(map(str, argv))
        record = dict(argv=argv, shell_command=shlex.join(argv), cwd=str(cwd))
        manifest['commands'].append(record)
        t0 = time.perf_counter()
        try:
            result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True,
                                    text=True, timeout=timeout)
            record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
            if result.returncode and not optional:
                raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr[-1500:]}')
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            record['error'] = str(exc)
            if not optional:
                raise
            return subprocess.CompletedProcess(argv, 1, '', str(exc))
        finally:
            record['runtime_seconds'] = round(time.perf_counter() - t0, 6)

    try:
        manifest['source_commit'] = command(['git', '-C', source, 'rev-parse', 'HEAD']).stdout.strip()
        files = source_files(source, language)
        if not files:
            raise ValueError('No eligible production sources')
        manifest.update(source_sha256=signature(source, files), source_files=len(files),
                        source_file_list=[p.relative_to(source).as_posix() for p in files],
                        ir_sha256=hashlib.sha256((directory/'ir_postprocessed.json').read_bytes()).hexdigest())
        # A stable, isolated mirror prevents tool directory traversal/imports from
        # silently widening the source scope to tests or vendored dependencies.
        work = reference / '_work'
        if work.exists():
            shutil.rmtree(work)
        inputs = work / 'source'
        inputs.mkdir(parents=True)
        for path in files:
            target = inputs / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        staged = [inputs / p.relative_to(source) for p in files]
        generated = work / 'classes.puml'
        generation_started = time.perf_counter()
        if language == 'python':
            manifest.update(tool='pyreverse', version=importlib.metadata.version('pylint'),
                            astroid_version=importlib.metadata.version('astroid'))
            argv = [Path(sys.executable).parent/'pyreverse', '-o', 'puml', '-f', 'ALL',
                    '-m', 'y', '-p', 'reference', '--ignore=', *staged]
            command(argv, work)
            generated = work / 'classes_reference.puml'
            # Optional second baseline is isolated and never substitutes for a
            # successful static pyreverse baseline if imports need ML packages.
            py2 = TOOLS/'py2puml-venv/bin/python'
            if py2.exists():
                command([py2, '-m', 'py2puml', inputs/'Lyra-1/src', 'src'], work, optional=True)
        elif language == 'javascript':
            manifest.update(tool='tplant', version=json.loads((PACKAGE/'node_modules/tplant/package.json').read_text())['version'],
                            typescript_version=json.loads((PACKAGE/'node_modules/typescript/package.json').read_text())['version'])
            manifest['node_version'] = command(['node', '--version']).stdout.strip()
            write_json(work/'tsconfig.json', {'compilerOptions': {'allowJs': True, 'checkJs': False,
                       'noEmit': True, 'noResolve': True, 'skipLibCheck': True, 'target': 'ESNext'},
                       'files': list(map(str, staged))})
            # tplant 3.1.3 accepts --project but ignores it; --input and the cwd
            # tsconfig are the functioning upstream CLI path for plain JS.
            command([PACKAGE/'node_modules/.bin/tplant', '--project', work/'tsconfig.json',
                     '--input', str(inputs/'**/*.js'), '--only-classes', '--associations', '--output', generated], work)
            manifest['tool_limitation'] = 'ES class declarations only; dynamic/prototype construction and constructor-assigned JS fields may be omitted.'
        elif language == 'java':
            java = java_executable()
            manifest['java_version'] = command([java, '-version']).stderr.strip()
            jar = TOOLS/'java2plantuml/java2plantuml.jar'
            attempt = command([java, '-jar', jar, inputs], work, optional=True)
            candidate = work/'output.puml'
            if attempt.returncode == 0 and candidate.exists() and not attempt.stderr.strip():
                generated = candidate
                manifest.update(tool='java2plantuml', version=command(['git', '-C', jar.parent, 'rev-parse', 'HEAD']).stdout.strip())
            else:
                manifest['java2plantuml_failure'] = 'No complete error-free output; see exact command stderr.'
                manifest.update(tool='javac-ast-adapter', version=manifest['java_version'],
                                tool_limitation='Custom adapter over OpenJDK parse API; NOT an established third-party RE product. No semantic attribution or project build.')
                raw = command([java, PACKAGE/'JavacReference.java', inputs], work)
                data = json.loads(raw.stdout)
                write_json(reference/'javac_ast.json', data)
                if data['failures']:
                    raise ValueError(f"javac reports {len(data['failures'])} parse failures")
                facts = javac_facts(data)
                generated.write_text(render_puml(facts))
                manifest['declared_parent_clauses'] = sum(len(c['parents']) for c in data['classes'])
                manifest['unresolved_or_external_parent_clauses'] = manifest['declared_parent_clauses'] - len(facts.inheritance)
        else:
            php = TOOLS/'php/php'
            phuml = TOOLS/'phuml.phar'
            manifest.update(tool='phuml', version='6.1.0')
            manifest['php_version'] = command([php, '--version']).stdout.strip()
            manifest['tool_sha256'] = hashlib.sha256(phuml.read_bytes()).hexdigest()
            command([php, phuml, 'phuml:plantuml', '-r', inputs, generated], work)
        manifest['generation_seconds'] = round(time.perf_counter() - generation_started, 6)
        if language == 'javascript':
            facts = read_tplant(generated.read_text())
        elif language != 'java' or manifest['tool'] != 'javac-ast-adapter':
            facts = read_puml(generated.read_text())
        if not facts.classes:
            raise ValueError('Tool emitted no class facts')
        output.mkdir()
        shutil.copyfile(generated, output/'classes.puml')
        write_json(reference/'reference_facts.json', facts.to_dict())
        parsed = parse_puml(generated.read_text(), 'class')
        manifest.update(status='ok', classes=len(facts.classes), inheritance=len(facts.inheritance),
                        relations=len(facts.inheritance)+len(facts.associations),
                        attributes=sum(len(c.attributes) for c in facts.classes),
                        methods=sum(len(c.methods) for c in facts.classes),
                        output_files=['reference/class/classes.puml'],
                        output_sha256={'classes.puml': hashlib.sha256(generated.read_bytes()).hexdigest()},
                        facts_sha256=hashlib.sha256((reference/'reference_facts.json').read_bytes()).hexdigest(),
                        parser_compatibility={'elements': parsed.element_count, 'relationships': parsed.relationship_count,
                                              'warnings': parsed.parse_warnings})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        manifest['failures'].append(f'{type(exc).__name__}: {exc}')
    manifest['runtime_seconds'] = round(time.perf_counter() - started, 6)
    write_json(reference/'reference_manifest.json', manifest)
    print(pid, manifest['status'], manifest.get('tool'), manifest.get('classes'), manifest['failures'], flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects', nargs='+')
    args = parser.parse_args()
    projects = json.loads((PACKAGE/'v2_corpus.json').read_text())['projects']
    if set(args.projects or []) - {p['id'] for p in projects}:
        parser.error('Unknown v2 project')
    results = [run(p) for p in projects if not args.projects or p['id'] in args.projects]
    sys.exit(any(r['status'] != 'ok' for r in results))


if __name__ == '__main__':
    main()
