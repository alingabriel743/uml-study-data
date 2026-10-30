"""Type-aware PlantUML reader for evaluation, not a PlantUML compiler.

Named diagrams use scoped identities and two-pass endpoint resolution. Activity
diagrams expose a control-flow graph. Unsupported control syntax sets
activity_graph_complete=False, with reasons in parse_warnings; consumers should
suppress activity SCI for those graphs. Preprocessing is not expanded.
"""

import re
from dataclasses import dataclass, field
from typing import Iterator


DECLARATION_KEYWORDS = (
    "abstract class", "abstract", "class", "interface", "enum", "annotation", "entity",
    "actor", "usecase", "participant", "boundary", "control", "database", "collections",
    "queue", "component", "node", "rectangle", "cloud", "artifact", "package", "folder",
    "frame", "stack", "card", "agent", "storage", "file", "person", "system", "container",
    "namespace", "together", "box", "partition", "object", "record", "struct", "state",
)
GROUPING_KEYWORDS = {"package", "folder", "frame", "namespace", "together", "box", "partition"}
_MEMBER_BODIES = {"abstract class", "abstract", "class", "interface", "enum", "annotation",
                  "entity", "object", "record", "struct"}
_QUOTED = r'"(?:\\.|[^"\\])*"'
_IDENTIFIER = r'[\w$]+(?:(?:[.\\]|::)[\w$]+)*'
_SEGMENT = rf'(?:{_QUOTED}|{_IDENTIFIER})'
_QUALIFIED = rf'{_SEGMENT}(?:(?:::|\.){_SEGMENT})*'
_ENDPOINT = rf'(?:{_QUALIFIED}|\([^)]*\)|\[[^\]]*\]|:[^:]+:)'
_TOKEN_RE = re.compile(_ENDPOINT)
_DECL_RE = re.compile(r'^(?P<kw>' + '|'.join(
    re.escape(k) for k in sorted(DECLARATION_KEYWORDS, key=len, reverse=True)
) + r')\b\s*', re.IGNORECASE)
_STEREOTYPE = re.compile(r'<<.*?>>', re.DOTALL)
_ARROW = r'(?:[<|*o+x#/{\\}]*[-.]+(?:\[[^\]\n]*\])?(?:left|right|up|down|le|ri|do)?[-.]*[>|*o+x#/{\\}]*)'
_REL_RE = re.compile(
    rf'^(?P<src>{_ENDPOINT}|[\[\]?])\s*(?:{_QUOTED}\s*)?'
    rf'(?P<arrow>{_ARROW})\s*(?:{_QUOTED}\s*)?'
    rf'(?P<dst>{_ENDPOINT}|[\[\]?])'
    r'\s*(?P<suffix>(?:(?:\*\*|\+\+|--|!!)(?:\s*#[\w]+)?\s*)*)'
    r'(?::\s*(?P<label>.*))?$', re.DOTALL,
)
_ACTION_START = re.compile(r'^(?:#[\w.\\/;-]+\s*)?:')
_MEMBER_MODIFIER = re.compile(r'\{\s*(?:static|abstract|field|method)\s*\}', re.I)
_MODIFIERS = re.compile(
    r'^(?:(?:public|private|protected|package|static|abstract|final|readonly|'
    r'volatile|transient|synchronized|native|default|const|var|val|function|def|fun)\s+)+'
)


@dataclass
class Member:
    """A listed member, without claiming its signature is correct."""

    name: str
    kind: str  # attribute or method
    type: str = ""
    parameters: str | None = None
    return_type: str = ""


def split_top_level(text: str, delimiter: str = ',') -> list[str]:
    """Split declarations without splitting generic arguments or initializers."""
    parts: list[str] = []
    depth = 0
    quote = ''
    escaped = False
    start = 0
    for i, char in enumerate(text):
        if quote:
            if char == quote and not escaped:
                quote = ''
            escaped = char == '\\' and not escaped
            continue
        if char in '\"\'':
            quote = char
        elif char in '<([{':
            depth += 1
        elif char in '>)]}':
            depth = max(0, depth - 1)
        elif char == delimiter and not depth:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def parse_members(line: str) -> list[Member]:
    """Read common UML/Java/PHP member notation, including multi-field declarations.

    Types and parameter lists are retained as text. Bodies, signatures, visibility
    and overload resolution are not evaluated here. A bare name is an attribute.
    """
    line = line.strip()
    if (not line or re.match(r'^(?:--|\.\.|==|__)(?:\s|$)', line)
            or re.fullmatch(r'[-.= _]{2,}', line)
            or line.startswith(("'", '//', '/*'))):
        return []
    force_field = bool(re.search(r'\{\s*field\s*\}', line, re.I))
    force_method = bool(re.search(r'\{\s*method\s*\}', line, re.I))
    line = _MEMBER_MODIFIER.sub('', line).strip()
    line = re.sub(r'^[+\-#~]\s*', '', line)
    line = re.sub(r'^(?:@[\w.]+(?:\([^)]*\))?\s*)+', '', line)
    line = _MODIFIERS.sub('', line).strip().rstrip(';').strip()
    # Ignore an initializer before looking for a method. A field may be
    # initialized with a constructor or function call.
    declaration = split_top_level(line, '=')[0].strip()
    method = None if force_field else re.match(r'^(.*?)\((.*)\)\s*(.*)$', declaration)
    if method:
        head, parameters, tail = method.groups()
        name = re.search(r'([\w$~]+)(?:<[^<>]*>)?\s*$', head)
        if not name:
            return []
        prefix_type = head[:name.start()].strip()
        return_type = tail.lstrip(':').strip() if tail.startswith(':') else prefix_type
        return [Member(name[1].lstrip('$'), 'method', parameters=parameters,
                       return_type=return_type)]
    if force_method:
        name = re.fullmatch(r'[\w$~]+', declaration)
        return [Member(declaration.lstrip('$'), 'method')] if name else []
    # A single colon is UML's name/type separator, not a namespace's ::.
    colon = re.search(r'(?<!:):(?!:)', declaration)
    if colon:
        name, type_name = declaration[:colon.start()].strip(), declaration[colon.end():].strip()
        if re.fullmatch(r'\$?\w+', name):
            return [Member(name.lstrip('$'), 'attribute', type_name)]
        return []
    fields = split_top_level(line)
    first = split_top_level(fields[0], '=')[0].strip()
    match = re.fullmatch(r'(?:(.+?)\s+)?(\$?[\w]+)\s*((?:\[\s*\])*)', first)
    if not match:
        return []
    type_name, name, suffix = match.groups()
    members = [Member(name.lstrip('$'), 'attribute', (type_name or '') + suffix)]
    for extra in fields[1:]:
        extra = split_top_level(extra, '=')[0].strip()
        match = re.fullmatch(r'(\$?\w+)\s*((?:\[\s*\])*)', extra)
        if match:
            members.append(Member(match[1].lstrip('$'), 'attribute', (type_name or '') + match[2]))
    return members


@dataclass
class Element:
    name: str
    kind: str
    alias: str = ""
    label: str = ""
    qualified_name: str = ""
    members: list[Member] = field(default_factory=list)
    unparsed_members: list[str] = field(default_factory=list)

    def names(self) -> set[str]:
        values = {n for n in (self.name, self.alias, self.label, self.qualified_name) if n}
        # Keep plain names available for evaluate.py's IR matching.
        return values | {_simple(n) for n in values}


@dataclass
class Relationship:
    src: str
    dst: str
    arrow: str
    label: str = ""
    src_external: bool = False
    dst_external: bool = False

    @property
    def external(self) -> bool:
        return self.src_external or self.dst_external

    @property
    def is_generalization(self) -> bool:
        return "|>" in self.arrow or "<|" in self.arrow


@dataclass
class ParsedDiagram:
    elements: dict[str, Element] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    # Retained for compatibility: now summaries of activity nodes/edges stored
    # above, not additional counts.
    actions: int = 0
    activity_flows: int = 0
    activity_graph_complete: bool = True
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def element_count(self) -> int:
        return len(self.elements)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)


def _strip(token: str) -> str:
    token = token.strip()
    if re.fullmatch(_QUOTED, token):
        return token[1:-1].strip()
    if len(token) >= 2 and (token[0], token[-1]) in {('(', ')'), ('[', ']'), (':', ':')}:
        token = token[1:-1]
    return token.strip()


def _identity(token: str) -> str:
    """Normalize separators while preserving quoted display text."""
    return re.sub(r'::|\.', '::', re.sub(_QUOTED, lambda m: _strip(m[0]), token)).strip()


def _simple(name: str) -> str:
    return re.split(r'::|[.\\]', name)[-1]


def _quotes_open(text: str) -> bool:
    return len(re.findall(r'(?<!\\)(?:\\\\)*"', text)) % 2 == 1


def _action_complete(text: str) -> bool:
    # Semicolons inside a continued parenthesized/bracketed label are text.
    plain = re.sub(_QUOTED, '', text)
    return (bool(re.search(r';\s*(?:<<.*?>>)?\s*$', text)) and
            plain.count('(') <= plain.count(')') and
            plain.count('[') <= plain.count(']'))


def _structural_parts(line: str) -> Iterator[str]:
    """Separate container/member braces, never braces in labels or messages."""
    if _REL_RE.match(line):
        yield line
        return
    quoted = False
    bracket = 0
    literal_braces = 0
    start = 0
    escaped = False
    modifier_positions = {i for m in _MEMBER_MODIFIER.finditer(line)
                          for i in range(m.start(), m.end())}
    for i, char in enumerate(line):
        if char == '"' and not escaped:
            quoted = not quoted
        if not quoted:
            if char == '[':
                bracket += 1
            elif char == ']':
                bracket -= 1
            elif char == '{' and not bracket and i not in modifier_positions and (
                    literal_braces or (line[start:i].strip()
                    and not _DECL_RE.match(line[start:i].strip())
                    and not re.match(r'^(?:as\b|<<)', line[start:i].strip(), re.I)
                    and not re.fullmatch(_QUOTED, line[start:i].strip()))):
                # Object-shaped parameter/return types and field initializers
                # are member text, not nested class/container bodies.
                literal_braces += 1
            elif char == '}' and literal_braces:
                literal_braces -= 1
            elif char in '{}' and not bracket and i not in modifier_positions:
                if line[start:i].strip():
                    yield line[start:i].strip()
                yield char
                start = i + 1
        escaped = char == '\\' and not escaped
    if line[start:].strip():
        yield line[start:].strip()


def _iter_statements(text: str, diagram_type: str = "") -> Iterator[str]:
    """Join literal multiline labels/actions before recognizing any keywords."""
    block_comment = False
    skip_depth = 0
    frames: list[bool] = []  # one entry per open { }: True if it is a class-like member body
    previous = ""
    end_block = ""
    pending = ""
    action = False
    shorthand = False
    condition = False
    for raw in text.splitlines():
        line = raw.strip()
        if pending:
            pending += '\n' + line
            if action:
                complete = _action_complete(pending)
            elif condition:
                plain = re.sub(_QUOTED, '', pending)
                complete = plain.count('(') <= plain.count(')') and not _quotes_open(pending)
            elif shorthand:
                complete = ']' in line and not _quotes_open(pending)
            else:
                complete = not _quotes_open(pending)
            if not complete:
                continue
            line, pending = pending, ""
        else:
            if block_comment:
                if "'/" in line:
                    block_comment = False
                continue
            if line.startswith("/'"):
                block_comment = "'/" not in line[2:]
                continue
            if end_block:
                if re.match(end_block, line, re.IGNORECASE):
                    end_block = ""
                continue
            if skip_depth:
                skip_depth += line.count('{') - line.count('}')
                continue
            if not line or line.startswith(("'", '@', '!')):
                continue
            note = re.match(r'^(?:floating\s+)?(?:[rh]?note)\b', line, re.IGNORECASE)
            member_note = re.match(r'^(?:floating\s+)?(?:[rh]?note)\s+(?:left|right|top|bottom|as|\")', line, re.I)
            if note and (not any(frames) or member_note):
                if ':' not in re.sub(_QUOTED, '', line):
                    end_block = r'^end\s*(?:[rh]?note)\b'
                continue
            block = not any(frames) and re.match(r'^(legend|header|footer)\b|^(title|caption)\s*$', line, re.IGNORECASE)
            if block:
                end_block = r'^end\s*' + (block[1] or block[2]) + r'\b'
                continue
            if not any(frames) and re.match(r'^(?:skinparam|style|hide|show|remove|title|caption|legend|header|footer|left to right|top to bottom|scale|autonumber)\b', line, re.IGNORECASE):
                skip_depth = max(0, line.count('{') - line.count('}'))
                continue
            if line.lower().startswith('<style>'):
                if '</style>' not in line.lower():
                    end_block = r'^</style>'
                continue
            action = diagram_type == 'activity' and bool(
                _ACTION_START.match(line) or re.match(r'^backward\s*:', line, re.I))
            condition = diagram_type == 'activity' and bool(re.match(
                r'^(?:if|elseif|else\s+if|while|repeat\s+while|switch)\b', line, re.I))
            shorthand = line.startswith('[') and ']' not in line and diagram_type != 'sequence'
            plain = re.sub(_QUOTED, '', line)
            if ((action and not _action_complete(line)) or
                    (condition and plain.count('(') > plain.count(')')) or
                    shorthand or (not action and _quotes_open(line))):
                pending = line
                continue
        if diagram_type == 'activity':
            yield line
        else:
            for part in _structural_parts(line):
                if part == '{':
                    # inside a class-like body, directive keywords (header, note, show, ...) are member names
                    kw = _DECL_RE.match(previous)
                    frames.append(bool(kw) and kw['kw'].lower() in _MEMBER_BODIES)
                elif part == '}':
                    if frames:
                        frames.pop()
                else:
                    previous = part
                yield part
    if pending:
        yield pending


def _declaration(line: str) -> tuple[str, str, str, str] | None:
    match = _DECL_RE.match(line)
    if match:
        kind, rest = match['kw'].lower(), line[match.end():].strip()
    elif line.startswith('['):
        kind, rest = 'component', line
    elif line.startswith('('):
        kind, rest = 'usecase', line
    elif line.startswith(':'):
        kind, rest = 'actor', line
    else:
        return None
    first = _TOKEN_RE.match(rest)
    if not first:
        return None
    token = first[0]
    tail = _STEREOTYPE.sub('', rest[first.end():]).strip()
    alias_match = re.match(r'^as\s+(' + _ENDPOINT + ')', tail, re.IGNORECASE | re.DOTALL)
    name, alias = _strip(token), ''
    label = name if token.startswith(('"', '[', '(')) else ''
    if alias_match:
        second = alias_match[1]
        if second.startswith('"') and not token.startswith(('"', '[', '(')):
            # Identifier as "Display label": Identifier remains the ID.
            alias, label = name, _strip(second)
        else:
            alias = _strip(second)
    return kind, name, alias, label


def parse_puml(text: str, diagram_type: str) -> ParsedDiagram:
    diagram = ParsedDiagram()
    lines = list(_iter_statements(text, diagram_type))
    if diagram_type == 'activity':
        _parse_activity(lines, diagram)
        if re.search(r'^\s*!(?:include\w*|define|definelong|function|procedure)\b', text, re.MULTILINE | re.I):
            diagram.activity_graph_complete = False
            diagram.parse_warnings.append('Preprocessor expansion is not supported')
        return diagram
    # An alias or stereotype can continue a declaration on the following line.
    # Inside a class-like body a bare keyword (control, queue, state, ...) is a member name, not a declaration.
    joined: list[str] = []
    frames: list[bool] = []
    for line in lines:
        if line == '{':
            kw = _DECL_RE.match(joined[-1]) if joined else None
            frames.append(bool(kw) and kw['kw'].lower() in _MEMBER_BODIES)
        elif line == '}' and frames:
            frames.pop()
        if (joined and not any(frames) and line not in {'{', '}'} and joined[-1] not in {'{', '}'} and
                (_DECL_RE.match(joined[-1]) or joined[-1].startswith(('[', '('))) and
                (re.match(r'^(?:as\b|<<)', line, re.I) or
                 _DECL_RE.fullmatch(joined[-1]))):
            joined[-1] += '\n' + line
        else:
            joined.append(line)
    lines = joined

    scope = ''
    stack: list[tuple[str, bool, str]] = []
    member_body = False
    member_owner = ''
    pending_scope = ''
    pending_member = False
    pending_owner = ''
    edges: list[tuple[re.Match, str]] = []
    references: dict[str, set[str]] = {}

    def qualify(name: str, parent: str) -> str:
        name = _identity(name)
        return f'{parent}::{name}' if parent else name

    def add(name: str, kind: str, parent: str, alias: str = '', label: str = '') -> str:
        key = qualify(alias or name, parent)
        if key not in diagram.elements:
            diagram.elements[key] = Element(name, kind, alias, label, key)
        for ref in (alias or name, name, label):
            if ref:
                references.setdefault(qualify(ref, parent), set()).add(key)
        return key

    # Declarations first: forward references cannot create phantom elements.
    for line in lines:
        if line == '{':
            stack.append((scope, member_body, member_owner))
            scope, member_body = pending_scope or scope, member_body or pending_member
            member_owner = pending_owner or member_owner
            pending_scope, pending_member = '', False
            pending_owner = ''
            continue
        if line == '}':
            if stack:
                scope, member_body, member_owner = stack.pop()
            continue
        if member_body:
            if member_owner:
                element = diagram.elements[member_owner]
                members = parse_members(line)
                element.members.extend(members)
                if not members and not re.match(r'^(?:--|\.\.|==|__|[-.= _]+$)', line):
                    element.unparsed_members.append(line)
            continue
        rel = _REL_RE.match(line)
        if rel:
            edges.append((rel, scope))
            pending_scope, pending_member = '', False
            pending_owner = ''
            continue
        decl = _declaration(line)
        pending_scope, pending_member = '', False
        pending_owner = ''
        if decl:
            kind, name, alias, label = decl
            if kind in {'package', 'namespace'}:
                pending_scope = qualify(alias or name, scope)
            elif kind not in GROUPING_KEYWORDS:
                pending_owner = add(name, kind, scope, alias, label)
            pending_member = kind in _MEMBER_BODIES

    def resolve(raw: str, parent: str) -> tuple[str, bool]:
        if diagram_type == 'sequence' and raw in {'[', ']', '?'}:
            return '@external:' + {'[': 'left', ']': 'right', '?': 'unknown'}[raw], True
        name = _strip(raw)
        current = parent
        while True:
            candidate = qualify(name, current)
            if candidate in diagram.elements:
                return candidate, False
            candidates = references.get(candidate, set())
            if len(candidates) == 1:
                return next(iter(candidates)), False
            if not current:
                break
            current = current.rpartition('::')[0]
        # Unique aliases may be referenced from outside their package.
        candidates = {key for key, el in diagram.elements.items()
                      if _identity(name) in {_identity(el.alias or el.name), el.qualified_name}}
        if len(candidates) == 1:
            return next(iter(candidates)), False
        kind = 'usecase' if raw.startswith('(') else 'actor' if raw.startswith(':') else 'component' if raw.startswith('[') else 'implicit'
        return add(name, kind, parent), False

    for rel, parent in edges:
        if re.search(r'\[\s*hidden\s*\]', rel['arrow'], re.IGNORECASE):
            continue
        src, src_external = resolve(rel['src'], parent)
        dst, dst_external = resolve(rel['dst'], parent)
        diagram.relationships.append(Relationship(
            src, dst, rel['arrow'] + rel['suffix'].strip(),
            (rel['label'] or '').strip(), src_external, dst_external))
    return diagram


@dataclass
class _Control:
    kind: str
    entry: str
    exits: list[tuple[str, str]] = field(default_factory=list)
    false_source: str = ''
    has_else: bool = False
    breaks: list[tuple[str, str]] = field(default_factory=list)
    backward: list[str] = field(default_factory=list)


def _parse_activity(lines: list[str], diagram: ParsedDiagram) -> None:
    """Build structured CFGs, retaining parallel edges for empty branches.

    Decisions and merges are separate nodes. While tests receive back edges;
    repeat loops have an entry merge and a trailing test. kill/detach terminate
    the frontier without inventing a visible node. Arrow labels annotate the
    next edge. Unsupported/unbalanced constructs are flagged.
    """
    frontier: list[tuple[str, str]] = []
    stack: list[_Control] = []

    def warn(reason: str) -> None:
        diagram.activity_graph_complete = False
        diagram.parse_warnings.append(reason)

    def node(kind: str, label: str) -> str:
        key = f'activity:{len(diagram.elements) + 1}'
        diagram.elements[key] = Element(label, kind, label=label, qualified_name=key)
        return key

    def connect(sources: list[tuple[str, str]], target: str) -> None:
        for source, label in sources:
            diagram.relationships.append(Relationship(source, target, '->', label))

    def append(kind: str, label: str) -> str:
        nonlocal frontier
        key = node(kind, label)
        connect(frontier, key)
        frontier = [(key, '')]
        return key

    def branch_label(line: str, keyword: str, default: str) -> str:
        matches = list(re.finditer(r'\b' + keyword + r'\s*\(', line, re.IGNORECASE))
        if matches:
            start = matches[-1].end()
            depth = 1
            for i in range(start, len(line)):
                if line[i] == '(':
                    depth += 1
                elif line[i] == ')':
                    depth -= 1
                    if depth == 0:
                        return line[start:i]
        return default

    def frame(kinds: set[str], command: str) -> _Control | None:
        if not stack or stack[-1].kind not in kinds:
            warn(f'Unmatched {command}')
            return None
        return stack[-1]

    def merge(sources: list[tuple[str, str]], kind: str = 'merge') -> None:
        nonlocal frontier
        frontier = sources
        if sources:
            append(kind, kind)

    for line in lines:
        lower = line.lower().strip()
        action_match = _ACTION_START.match(line)
        if action_match:
            if not _action_complete(line):
                warn('Unterminated activity action')
            if re.search(r'\n(?:if|else|elseif|endif|fork|repeat|while|stop|endwhile)\b(?!\s*[:=])', line, re.I):
                warn('Control keyword absorbed by unterminated action')
            append('action', line[action_match.end():].rstrip(';').strip())
        elif re.match(r'^(?:start|stop|end)\s*$', lower):
            append(lower, lower)
            if lower != 'start':
                frontier = []
        elif lower in {'kill', 'detach'}:
            frontier = []
        elif re.match(r'^if\b', lower):
            key = append('decision', line)
            stack.append(_Control('if', key, false_source=key))
            frontier = [(key, branch_label(line, 'then', 'yes'))]
        elif re.match(r'^(?:elseif|else\s+if)\b', lower):
            control = frame({'if'}, 'elseif')
            if control:
                control.exits.extend(frontier)
                frontier = [(control.false_source, 'no')]
                key = append('decision', line)
                control.false_source = key
                frontier = [(key, branch_label(line, 'then', 'yes'))]
        elif re.match(r'^else\b', lower):
            control = frame({'if'}, 'else')
            if control:
                control.exits.extend(frontier)
                control.has_else = True
                frontier = [(control.false_source, branch_label(line, 'else', 'no'))]
        elif lower == 'endif':
            control = frame({'if'}, 'endif')
            if control:
                stack.pop()
                sources = control.exits + frontier
                if not control.has_else:
                    sources.append((control.false_source, 'no'))
                merge(sources)
        elif re.match(r'^switch\b', lower):
            key = append('decision', line)
            stack.append(_Control('switch', key))
            frontier = []
        elif re.match(r'^case\b', lower):
            control = frame({'switch'}, 'case')
            if control:
                control.exits.extend(frontier)
                frontier = [(control.entry, branch_label(line, 'case', line))]
        elif lower == 'endswitch':
            control = frame({'switch'}, 'endswitch')
            if control:
                stack.pop()
                merge(control.exits + frontier)
        elif lower in {'fork', 'split'}:
            key = append('fork', lower)
            stack.append(_Control(lower, key))
        elif lower in {'fork again', 'split again'}:
            control = frame({lower.split()[0]}, lower)
            if control:
                control.exits.extend(frontier)
                frontier = [(control.entry, '')]
        elif re.match(r'^end\s+(?:fork|split|merge)\b', lower):
            control = frame({'fork', 'split'}, lower)
            if control:
                stack.pop()
                merge(control.exits + frontier, 'merge' if lower.startswith('end merge') else 'join')
        elif re.match(r'^while\b', lower):
            key = append('decision', line)
            stack.append(_Control('while', key))
            frontier = [(key, branch_label(line, 'is', 'yes'))]
        elif re.match(r'^endwhile\b', lower):
            control = frame({'while'}, 'endwhile')
            if control:
                stack.pop()
                connect(frontier, control.entry)
                frontier = [(control.entry, branch_label(line, 'endwhile', 'no'))]
                if control.breaks:
                    merge(frontier + control.breaks)
        elif re.match(r'^repeat\s+while\b', lower):
            control = frame({'repeat'}, 'repeat while')
            if control:
                stack.pop()
                key = append('decision', line)
                frontier = [(key, branch_label(line, 'is', 'yes'))]
                for label in control.backward:
                    append('action', label)
                connect(frontier, control.entry)
                frontier = [(key, branch_label(line, 'not', 'no'))]
                if control.breaks:
                    merge(frontier + control.breaks)
        elif re.match(r'^repeat\b', lower):
            label = line[len('repeat'):].strip()
            key = append('action' if label.startswith(':') else 'merge', label.strip(':;') or 'repeat')
            stack.append(_Control('repeat', key))
        elif re.match(r'^backward\b', lower):
            control = frame({'repeat', 'while'}, 'backward')
            if control and control.kind == 'repeat':
                control.backward.append(line[len('backward'):].strip(':; '))
            else:
                warn('Unsupported backward action outside repeat')
        elif lower == 'break':
            control = next((c for c in reversed(stack) if c.kind in {'while', 'repeat'}), None)
            if control:
                control.breaks.extend(frontier)
                frontier = []
            else:
                warn('Break outside loop')
        elif re.match(r'^-+(?:\[[^]]*\])?>', line):
            label = line.split('>', 1)[1].strip().rstrip(';')
            frontier = [(key, label or old_label) for key, old_label in frontier]
        elif (line in {'{', '}'} or line.startswith('|') or
              line.startswith('==') or
              re.match(r'^(?:partition|group)\b', lower) or lower == 'end group'):
            continue
        else:
            warn('Unsupported activity statement: ' + line[:100])
    if stack:
        warn('Unclosed activity blocks: ' + ', '.join(c.kind for c in stack))
    diagram.actions = len(diagram.elements)
    diagram.activity_flows = len(diagram.relationships)
