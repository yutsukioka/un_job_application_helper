#!/usr/bin/env python3
"""Source-locked native UNU form-field transfers; no content selection or rendering."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'
SPACE = '{http://www.w3.org/XML/1998/namespace}space'
PART = 'word/document.xml'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(b):
    return hashlib.sha256(b).hexdigest()


def visible(node):
    return ''.join((n.text or '') if n.tag == W+'t' else '\n' if n.tag == W+'br' else '\t'
                   for n in node.iter() if n.tag in (W+'t', W+'br', W+'tab'))


def legacy_result(begin):
    run = begin.getparent()
    p = run.getparent()
    require(run.tag == W+'r' and p.tag == W+'p', 'Unsupported legacy field container')
    nodes = list(p); start = nodes.index(run); sep = None
    for n in nodes[start+1:]:
        markers = n.findall('.//w:fldChar', NS)
        for marker in markers:
            kind = marker.get(W+'fldCharType')
            require(kind != 'begin', 'Nested fields are unsupported')
            if kind == 'separate':
                require(sep is None, 'Duplicate separator')
                sep = nodes.index(n)
            if kind == 'end':
                require(sep is not None, 'Missing result separator')
                result = nodes[sep+1:nodes.index(n)]
                require(result and all(x.tag == W+'r' for x in result), 'Unsupported result structure')
                require(all(ch.tag in (W+'rPr', W+'t', W+'br', W+'tab') for x in result for ch in x),
                        'Unsafe result content')
                return result
    raise ValueError('Field end not found in original paragraph')


def field_info(root):
    tree = root.getroottree(); result = []
    for el in root.xpath('.//w:fldChar[w:ffData] | .//w:sdt[w:sdtPr/w:dropDownList]', namespaces=NS):
        row = next(iter(el.xpath('ancestor::w:tr[1]', namespaces=NS)), None)
        row_index = list(row.getparent()).index(row) if row is not None else None
        # Physical row index excludes tblPr/tblGrid.
        if row is not None:
            row_index = row.getparent().findall('w:tr', NS).index(row)
        path = tree.getpath(el)
        info = {'xpath': path, 'row': row_index, 'context': visible(row)[:350] if row is not None else ''}
        if el.tag == W+'sdt':
            info.update(kind='dropdown', expected=visible(el.find('w:sdtContent', NS)),
                        choices=[n.get(W+'displayText', n.get(W+'value')) for n in el.findall('w:sdtPr/w:dropDownList/w:listItem', NS)])
        else:
            ff = el.find('w:ffData', NS); name = ff.find('w:name', NS)
            info['name'] = name.get(W+'val') if name is not None else None
            if ff.find('w:textInput', NS) is not None:
                try:
                    info.update(kind='text', expected=''.join(visible(r) for r in legacy_result(el)))
                except ValueError as exc:
                    info.update(kind='unsupported', reason=str(exc))
            elif ff.find('w:checkBox', NS) is not None:
                box = ff.find('w:checkBox', NS); v = box.find('w:checked', NS)
                if v is None:
                    v = box.find('w:default', NS)
                info.update(kind='checkbox', expected=v is not None and v.get(W+'val', '1') in ('1', 'true', 'on'))
            else:
                info.update(kind='unsupported')
        result.append(info)
    return result


def load(source):
    data = source.read_bytes()
    with ZipFile(io.BytesIO(data)) as z:
        require(len(z.namelist()) == len(set(z.namelist())), 'Duplicate ZIP entries')
        root = E.fromstring(z.read(PART), E.XMLParser(resolve_entities=False, no_network=True))
    return data, root


def inspect(source):
    data, root = load(source)
    return {'source_sha256': digest(data), 'fields': field_info(root)}


def set_result(runs, value):
    require(isinstance(value, str) and '\r' not in value and '\t' not in value, 'Use text and LF line breaks only')
    require(all(ord(c) >= 32 or c == '\n' for c in value), 'Invalid XML control character')
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), 'Invalid Unicode surrogate')
    for run in runs:
        for ch in list(run):
            if ch.tag in (W+'t', W+'br', W+'tab'):
                run.remove(ch)
    for index, line in enumerate(value.split('\n')):
        if index:
            E.SubElement(runs[0], W+'br')
        t = E.SubElement(runs[0], W+'t'); t.set(SPACE, 'preserve'); t.text = line


def apply(source, manifest, output):
    data, root = load(source)
    require(digest(data) == manifest.get('source_sha256'), 'Source hash changed')
    require(source.resolve() != output.resolve() and not output.exists(), 'Output must be a new file')
    info = {f['xpath']: f for f in field_info(root)}; used = set(); audit = []
    operations = manifest.get('operations')
    require(isinstance(operations, list) and operations, 'No operations')
    for op in operations:
        path = op['xpath']; require(path not in used, 'Duplicate field operation'); used.add(path)
        require(path in info, 'Field not in inventory')
        f = info[path]; require(f['kind'] == op['kind'] and f['kind'] != 'unsupported', 'Wrong or unsupported field type')
        require(type(op['expected']) is type(f['expected']) and op['expected'] == f['expected'], 'Expected value mismatch')
        require(isinstance(op.get('source_ref'), str) and op['source_ref'].strip(), 'Source reference required')
        found = root.xpath(path, namespaces=NS); require(len(found) == 1, 'Ambiguous field'); el = found[0]
        require(not el.xpath('ancestor::w:ins | ancestor::w:del | ancestor::w:moveFrom | ancestor::w:moveTo', namespaces=NS), 'Tracked field unsupported')
        value = op['value']; row = el.xpath('ancestor::w:tr[1]', namespaces=NS)
        if row:
            label = ' '.join(visible(row[0]).split())
            require('SIGNATURE' not in label and 'I certify' not in label, 'Applicant certification is protected')
        item = {'xpath': path, 'source_ref': op['source_ref'], 'kind': f['kind']}
        if f['kind'] == 'text':
            require(isinstance(value, str), 'Text value required')
            count = len(value.split()); item.update(words=count, characters=len(value))
            limits = []
            if row:
                prev = row[0].getprevious()
                label = ' '.join(visible(prev).split()) if prev is not None else ''
                if 'DUTIES AND RELATED ACCOMPLISHMENTS' in label:
                    limits.append(300)
                if 'Motivational Statement' in label:
                    limits.append(600)
            if 'word_limit' in op:
                require(type(op['word_limit']) is int and op['word_limit'] > 0, 'Invalid word limit')
                limits.append(op['word_limit'])
            require(not limits or count <= min(limits), 'Word limit exceeded')
            if 'char_limit' in op:
                require(type(op['char_limit']) is int and op['char_limit'] > 0, 'Invalid character limit')
                require(len(value) <= op['char_limit'], 'Character limit exceeded')
            runs = legacy_result(el)
            require(len({E.tostring(r.find('w:rPr', NS)) for r in runs}) == 1, 'Mixed result formatting; inspect manually')
            set_result(runs, value)
            # Keep the field's stored default in sync with its visible result.
            text_input = el.find('w:ffData/w:textInput', NS); default = text_input.find('w:default', NS)
            if default is None:
                default = E.Element(W+'default')
                typ = text_input.find('w:type', NS)
                text_input.insert(1 if typ is not None else 0, default)
            default.set(W+'val', value)
        elif f['kind'] == 'dropdown':
            require(isinstance(value, str) and value in f['choices'], 'Invalid native dropdown choice')
            content = el.find('w:sdtContent', NS); runs = list(content)
            require(runs and all(r.tag == W+'r' for r in runs), 'Unsupported dropdown structure')
            require(all(ch.tag in (W+'rPr', W+'t') for r in runs for ch in r), 'Unsafe dropdown content')
            set_result(runs, value)
            pr = el.find('w:sdtPr', NS)
            for placeholder in pr.findall('w:showingPlcHdr', NS):
                pr.remove(placeholder)
            choices = pr.findall('w:dropDownList/w:listItem', NS)
            chosen = next(n for n in choices if n.get(W+'displayText', n.get(W+'value')) == value)
            pr.find('w:dropDownList', NS).set(W+'lastValue', chosen.get(W+'value'))
        else:
            require(type(value) is bool, 'Checkbox value must be boolean')
            box = el.find('w:ffData/w:checkBox', NS); checked = box.find('w:checked', NS)
            if checked is None:
                checked = E.SubElement(box, W+'checked')
            checked.set(W+'val', '1' if value else '0')
        audit.append(item)
    final_info = {f['xpath']: f for f in field_info(root)}
    require(set(final_info) == set(info), 'Form inventory changed')
    for path, old in info.items():
        if path not in used:
            require(final_info[path].get('expected') == old.get('expected'), 'Unselected field changed')
    for op in operations:
        require(final_info[op['xpath']]['expected'] == op['value'], 'Field readback mismatch')
    xml = E.tostring(root, encoding='UTF-8', xml_declaration=True, standalone=True)
    buf = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as src, ZipFile(buf, 'w') as dst:
        dst.comment = src.comment
        for entry in src.infolist():
            dst.writestr(entry, xml if entry.filename == PART else src.read(entry.filename))
    result = buf.getvalue()
    with ZipFile(io.BytesIO(data)) as src, ZipFile(io.BytesIO(result)) as dst:
        require(src.namelist() == dst.namelist(), 'Package inventory changed')
        for name in src.namelist():
            if name != PART:
                require(src.read(name) == dst.read(name), 'Unrelated ZIP part changed')
    require(source.read_bytes() == data, 'Source changed during transfer')
    with output.open('xb') as stream:
        stream.write(result)
    return {'source_sha256': digest(data), 'output_sha256': digest(result), 'operations': audit,
            'unselected_field_values_unchanged': True, 'unrelated_parts_byte_identical': True,
            'visual_qa': 'NOT_PERFORMED_BY_HELPER'}


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('inspect'); i.add_argument('input', type=Path)
    a = sub.add_parser('apply'); a.add_argument('input', type=Path); a.add_argument('manifest', type=Path); a.add_argument('output', type=Path); a.add_argument('--report', type=Path)
    args = p.parse_args()
    try:
        if getattr(args, 'report', None):
            require(args.report.resolve() not in (args.input.resolve(), args.output.resolve(), args.manifest.resolve()), 'Unsafe report path')
            require(not args.report.exists() and not args.report.is_symlink(), 'Report must be a new file')
        result = inspect(args.input) if args.command == 'inspect' else apply(args.input, json.loads(args.manifest.read_text()), args.output)
        payload = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
        if getattr(args, 'report', None):
            with args.report.open("x", encoding="utf-8") as report:
                report.write(payload)
        print(payload, end='')
    except (ValueError, KeyError, OSError, E.Error) as exc:
        p.exit(2, str(exc)+'\n')


if __name__ == '__main__':
    main()
