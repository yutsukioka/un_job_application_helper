"""Read Taleo's public DOM/value bindings without executing source JavaScript."""
from __future__ import annotations

import ast
from collections import defaultdict
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import unquote


class _Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()

    def text(self):
        return ''.join(c.text() if isinstance(c, _Node) else c for c in self.children)


class _DOM(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def _literal_list(value: str) -> list[str]:
    try:
        result = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError('Malformed Taleo public binding array') from exc
    if not isinstance(result, list) or any(not isinstance(x, str) for x in result):
        raise ValueError('Unsupported Taleo public binding values')
    return result


def public_bindings(source: str, values: list[str]) -> dict[str, Any] | None:
    """Return visible role values and exact semantic/DOM mapping evidence.

    List properties targeting absent DOM nodes are control parameters, such as
    WIPO's hidden encoded sites address; they are not public description fields.
    Custom semantic fields are retained only when bound to an actual DOM node.
    """
    matches = list(re.finditer(r'\bdescRequisition\s*:\s*\{\s*_size\s*:\s*1\s*,\s*'
                              r'_hles\s*:\s*(\[[^\]]*\])\s*,\s*_hlid\s*:\s*(\[[^\]]*\])', source))
    if not matches:
        if re.search(r'\bdescRequisition\s*:\s*\{', source):
            raise ValueError('Unsupported Taleo public requisition binding schema')
        return None
    if len(matches) != 1:
        raise ValueError('Ambiguous Taleo public requisition binding schema')
    targets, semantics = map(_literal_list, matches[0].groups())
    if not values or len(targets) != len(semantics) or len(targets) != len(values) or len(set(targets)) != len(targets):
        raise ValueError('Taleo public binding arrays are not exactly aligned')
    dom = _DOM(source)
    nodes = defaultdict(list)
    dom_order = {}
    for position, node in enumerate(dom.root.nodes()):
        if node.attrs.get('id'):
            nodes[node.attrs['id']].append(node)
            dom_order[node.attrs['id']] = position
    labels = {}
    outer = list(re.finditer(r'\brequisitionDescriptionInterface\s*:\s*\{\s*_ctls\s*:\s*\[[^\]]*\]\s*,\s*_hles\s*:\s*(\[[^\]]*\])', source))
    fills = list(re.finditer(r'api\.fillInterface\([\'\"]requisitionDescriptionInterface[\'\"]\s*,\s*(\[.*?\])\s*\);', source, re.S))
    if outer or fills:
        if len(outer) != 1 or len(fills) != 1:
            raise ValueError('Ambiguous Taleo public label bindings')
        keys, text = _literal_list(outer[0][1]), _literal_list(fills[0][1])
        if len(keys) != len(text) or len(set(keys)) != len(keys):
            raise ValueError('Taleo public label arrays are not exactly aligned')
        labels = dict(zip(keys, text, strict=True))
    visible, excluded = [], []
    for index, (target, semantic, value) in enumerate(zip(targets, semantics, values, strict=True)):
        if not semantic.startswith('reqlistitem.'):
            raise ValueError('Unknown Taleo public semantic namespace')
        matching = nodes.get('requisitionDescriptionInterface.' + target, [])
        if not matching:
            excluded.append({'index':index, 'target':target, 'semantic':semantic, 'reason':'no public DOM target'})
            continue
        if len(matching) != 1:
            raise ValueError('Duplicate Taleo public DOM target')
        node = matching[0]
        if node.tag in {'script','style','input'}:
            excluded.append({'index':index, 'target':target, 'semantic':semantic, 'reason':'non-public control DOM target'})
            continue
        if any(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden', p.attrs.get('style',''), re.I)
               or 'hidden' in p.attrs for p in _ancestors(node)):
            excluded.append({'index':index, 'target':target, 'semantic':semantic, 'reason':'explicitly hidden DOM target'})
            continue
        # A visible role field must belong to the public title or a content row.
        if not (semantic in {'reqlistitem.title','reqlistitem.contestnumber','reqlistitem.description','reqlistitem.qualification'}
                or re.fullmatch(r'reqlistitem\.(?:G\d+|primarylocation|otherlocations|organization|postingdate|unpostingdate|closedate|jobschedule|jobtype|jobfield)', semantic)):
            excluded.append({'index':index, 'target':target, 'semantic':semantic, 'reason':'public action/control outside role-field namespace'})
            continue
        row = next((p for p in _ancestors(node) if 'contentlinepanel' in p.attrs.get('class','').split()), None)
        public_labels = []
        if row:
            for child in row.nodes():
                if child is node:
                    break
                if 'subtitle' not in child.attrs.get('class','').split():
                    continue
                key = child.attrs.get('id','').removeprefix('requisitionDescriptionInterface.')
                text = unquote(labels.get(key, child.text())).strip()
                if text and text not in public_labels:
                    public_labels.append(text)
        # Some public rows contain two successive labelled values (WIPO
        # publication and deadline). The nearest preceding subtitle binds each.
        visible.append({'index':index, 'target':target, 'semantic':semantic,
                        'public_label':public_labels[-1] if public_labels else None, 'encoded_value':value})
    if not {'reqlistitem.title','reqlistitem.contestnumber','reqlistitem.description'} <= {v['semantic'] for v in visible}:
        raise ValueError('Missing public Taleo title/identity/description targets')
    visible.sort(key=lambda field: dom_order['requisitionDescriptionInterface.' + field['target']])
    return {'kind':'paired_public_dom_bindings', 'array_length':len(values), 'visible_fields':visible,
            'excluded_control_bindings':excluded, 'label_binding_count':len(labels)}


def _ancestors(node):
    while node is not None:
        yield node
        node = node.parent
