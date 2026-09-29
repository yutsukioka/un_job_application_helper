#!/usr/bin/env node
// Invoke with the bundled runtime, from a run-dir containing its node_modules
// symlink. The artifact dependency is loaded from that run-dir, never the repo.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const options = {};
for (let i = 2; i < process.argv.length; i++) {
  const flag = process.argv[i];
  if (['--inspect', '--edit', '--verify'].includes(flag)) options.mode = flag.slice(2);
  else if (flag.startsWith('--') && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')) options[flag.slice(2)] = process.argv[++i];
  else throw new Error(`Unknown/missing argument: ${flag}`);
}
for (const k of ['mode', 'source', 'curated', 'run-dir', 'as-of']) if (!options[k]) throw new Error(`Missing --${k}`);
const run = path.resolve(options['run-dir']);
await fs.mkdir(run, {recursive: true});
const requireFromRun = createRequire(path.join(run, 'artifact_runtime_loader.cjs'));
const {FileBlob, SpreadsheetFile} = await import(pathToFileURL(requireFromRun.resolve('@oai/artifact-tool')).href);
const helper = path.join(path.dirname(fileURLToPath(import.meta.url)), 'preserve_workbook.py');
const python = options.python || process.env.CODEX_BUNDLED_PYTHON;
if (!python) throw new Error('Pass --python with the load_workspace_dependencies Python executable.');
function py(mode) {
  const args = [helper, mode, '--run-dir', run];
  if (mode === 'prepare') args.push('--source', path.resolve(options.source), '--curated', path.resolve(options.curated), '--as-of', options['as-of']);
  const result = spawnSync(python, args, {encoding: 'utf8'});
  if (result.status !== 0) throw new Error(result.stderr || result.stdout || 'Python helper failed');
  console.log(result.stdout.trim());
}
if (options.mode === 'inspect') py('prepare');
const m = JSON.parse(await fs.readFile(path.join(run, 'manifest.json'), 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
if (hash(await fs.readFile(m.source)) !== m.source_sha256) throw new Error('Source changed after inspection; start a fresh run.');
if (hash(await fs.readFile(m.curated)) !== m.curated_sha256) throw new Error('Curated input changed after inspection; start a fresh run.');
if (options['as-of'] !== m.as_of || path.resolve(options.source) !== m.source) throw new Error('CLI differs from inspected manifest.');
const source = options.mode === 'verify' ? path.join(run, 'verified.xlsx') : path.join(run, 'before.xlsx');
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(source));
const changes = {sheets: {}};
const planName = m.roles.plan;
const qsheet = n => `'${n.replaceAll("'", "''")}'`;
const date = s => s ? new Date(s + (s.includes('T') ? 'Z' : 'T00:00:00Z')) : null;
const column = n => n < 26 ? String.fromCharCode(65 + n) : 'A' + String.fromCharCode(65 + n - 26);
const cfg = name => changes.sheets[name] ||= {updated: [], appended: [], formats: {}, links: {}, formulas: []};
function write(name, address, value, formula = false, fmt = null) {
  const sh = wb.worksheets.getItem(name);
  sh.getRange(address)[formula ? 'formulas' : 'values'] = [[value]];
  const c = cfg(name);
  const row = Number(address.match(/\d+$/)[0]);
  if (row <= m.sheets[name].last && !c.updated.includes(address)) c.updated.push(address);
  if (formula && !c.formulas.includes(address)) c.formulas.push(address);
  if (fmt) { sh.getRange(address).setNumberFormat(fmt); c.formats[address] = fmt; }
}
function countdown(row, precision) {
  const a = `A${row}`;
  const active = precision === 'date'
    ? `IF(${a}<TODAY(),"Closed",IF(${a}=TODAY(),"Today; time unknown",(${a}-TODAY())&" days; time unknown"))`
    : `IF(${a}<=NOW(),"Closed",INT(${a}-NOW())&" days "&INT(MOD((${a}-NOW())*24,24))&" hours")`;
  return `=IF(NOT(ISNUMBER(${a})),"Deadline unknown",${active})`;
}
async function render(name, range, file) {
  const blob = await wb.render({sheetName: name, range, scale: 1, format: 'png'});
  await fs.writeFile(path.join(run, file), new Uint8Array(await blob.arrayBuffer()));
}
if (options.mode === 'inspect') {
  await fs.writeFile(path.join(run, 'before_inspect.ndjson'), (await wb.inspect({kind: 'workbook,sheet,table', maxChars: 4500, tableMaxRows: 3, tableMaxCols: 5})).ndjson);
  await render(planName, `B${Math.max(10, m.sheets[planName].last - 2)}:J${m.sheets[planName].last}`, 'before_plan.png');
  for (const kind of ['vacancy', 'roster']) {
    const name = m.roles[kind], last = m.sheets[name].last;
    await render(name, `A${Math.max(7, last - 1)}:I${last}`, `before_${kind}.png`);
  }
  console.log('Read-only preparation complete. Inspect the before PNGs, run the required artifact-operation marker once, then use --edit.');
} else if (options.mode === 'edit') {
  const fieldCols = {fit: 'G', why: 'H', gap: 'I', requirements: 'K', prep: 'L', deadline: 'M', evidence: 'N'};
  const metadataCols = {title: 'C', duty: 'D', grade: 'E', contract: 'F'};
  for (const r of m.records) {
    const sh = wb.worksheets.getItem(r.sheet), p = wb.worksheets.getItem(planName);
    const row = r.row, pr = r.plan_row;
    const added = r.action === 'append';
    if (added) {
      sh.getRange(`A${row}:Q${row}`).copyFrom(sh.getRange(`A${m.sheets[r.sheet].template}:Q${m.sheets[r.sheet].template}`), 'all');
      p.getRange(`B${pr}:AD${pr}`).copyFrom(p.getRange(`B${m.sheets[planName].template}:AD${m.sheets[planName].template}`), 'all');
      cfg(r.sheet).appended.push(row); cfg(planName).appended.push(pr);
      const values = [date(r.date_local), r.org, r.title, r.duty, r.grade, r.contract, r.fit, r.why, r.gap, 'Apply', r.requirements, r.prep, r.deadline, r.evidence, r.id, 'View notice'];
      sh.getRange(`A${row}:P${row}`).values = [values];
      // IDs are identifiers, including values longer than Excel's 15-digit
      // numeric precision. Keep their complete display and string storage.
      write(r.sheet, `O${row}`, String(r.id), false, '@');
      p.getRange(`B${pr}:H${pr}`).values = [[r.org, r.title, r.owner ?? m.owner, null, r.priority, date(m.as_of), date(r.date_local)]];
      write(planName, `I${pr}`, `=IF(OR(G${pr}="",H${pr}="",H${pr}<G${pr}),"",INT(H${pr})-INT(G${pr})+1)`, true);
      // Stable join was resolved from ID and hyperlinks by the preparation step.
      write(planName, `J${pr}`, `=${qsheet(r.sheet)}!Q${row}`, true);
      for (let ci = 10; ci < 30; ci++) {
        const c = column(ci);
        write(planName, `${c}${pr}`, `=IF(OR($G${pr}="",$H${pr}="",$H${pr}<$G${pr}),"",IF(AND(${c}$9<=INT($H${pr}),${c}$9+6>=INT($G${pr})),1,""))`, true);
      }
      cfg(r.sheet).links[`J${row}`] = r.apply_url;
      cfg(r.sheet).links[`P${row}`] = r.notice_url;
      cfg(planName).links[`C${pr}`] = r.apply_url;
    } else {
      for (const [field, col] of Object.entries(fieldCols)) if (field in r) write(r.sheet, `${col}${row}`, r[field]);
      if ('priority' in r) write(planName, `F${pr}`, r.priority);
      if (r.action === 'refresh') {
        for (const [field, col] of Object.entries(metadataCols)) if (field in r) write(r.sheet, `${col}${row}`, r[field]);
        if ('title' in r) write(planName, `C${pr}`, r.title);
        if ('apply_url' in r) { cfg(r.sheet).links[`J${row}`] = r.apply_url; cfg(planName).links[`C${pr}`] = r.apply_url; }
        if ('notice_url' in r) cfg(r.sheet).links[`P${row}`] = r.notice_url;
      }
    }
    if (added || (r.action === 'refresh' && 'date_local' in r)) {
      const fmt = r.date_precision === 'time' ? 'dd mmm yyyy hh:mm:ss' : 'dd mmm yyyy';
      write(r.sheet, `A${row}`, date(r.date_local), false, fmt);
      write(r.sheet, `Q${row}`, countdown(row, r.date_precision), true);
      write(planName, `H${pr}`, date(r.date_local), false, fmt);
      if (added) write(planName, `G${pr}`, date(m.as_of), false, 'dd mmm yyyy');
    }
  }
  if (m.records.length) {
    const added = kind => m.records.filter(r => r.action === 'append' && r.kind === kind).length;
    const total = kind => m.sheets[m.roles[kind]].count + added(kind);
    const end = m.sheets[planName].new_last;
    write(planName, 'C5', `${total('vacancy')} vacancies + ${total('roster')} roster calls (including retained entries)`);
    write(planName, 'K5', 'TRACKED OPTIONS');
    write(planName, 'S2', `Updated ${new Date(m.as_of + 'T12:00:00Z').toLocaleDateString('en-GB', {day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'})}`);
    write(planName, 'K6', `=COUNTA($C$10:$C$${end})`, true);
    for (const [cell, col, value] of [['O6', 'E', 'Complete'], ['S6', 'E', 'In Progress'], ['W6', 'E', 'At Risk'], ['AA6', 'F', 'P0']]) write(planName, cell, `=COUNTIF($${col}$10:$${col}$${end},"${value}")`, true);
    for (const kind of ['vacancy', 'roster']) {
      const name = m.roles[kind], touched = m.records.filter(r => r.kind === kind);
      if (touched.length) write(name, 'A3', `${m.sheets[name].count} retained + ${added(kind)} added; ${touched.length - added(kind)} existing reviewed. Updated ${m.as_of}; existing order and application status preserved.`);
    }
  }
  wb.recalculate();
  const errors = await wb.inspect({kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!', options: {useRegex: true, maxResults: 100}, maxChars: 4000});
  await fs.writeFile(path.join(run, 'formula_error_scan.ndjson'), errors.ndjson);
  await fs.writeFile(path.join(run, 'author_manifest.json'), JSON.stringify(changes, null, 2));
  await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(run, 'authored.xlsx'));
  py('overlay');
  console.log('Prepared verified.xlsx; original remains unchanged. Use --verify and inspect the resulting PNGs before installation.');
} else {
  const summary = await wb.inspect({kind: 'table', range: `${qsheet(planName)}!K5:AD6`, include: 'values,formulas', maxChars: 3000, tableMaxRows: 2, tableMaxCols: 20});
  await fs.writeFile(path.join(run, 'verified_inspect.ndjson'), summary.ndjson);
  for (const [name, meta] of Object.entries(m.sheets)) {
    const touched = m.records.map(r => name === planName ? r.plan_row : r.sheet === name ? r.row : null).filter(Boolean);
    const rows = [...new Set(touched)].sort((a, b) => a-b);
    for (let i = 0; i < rows.length; i += 3) {
      const chunk = rows.slice(i, i+3), start = chunk[0], end = chunk.at(-1);
      // Avoid huge images when reviewed rows are far apart.
      if (end-start > 5) { for (const row of chunk) await render(name, `${meta.role === 'plan' ? 'B' : 'A'}${row}:${meta.role === 'plan' ? 'J' : 'I'}${row}`, `verified_${meta.role}_${row}.png`); }
      else await render(name, `${meta.role === 'plan' ? 'B' : 'A'}${start}:${meta.role === 'plan' ? 'J' : 'I'}${end}`, `verified_${meta.role}_${start}_${end}.png`);
      if (meta.role !== 'plan') for (const row of chunk) await render(name, `K${row}:Q${row}`, `verified_${meta.role}_requirements_${row}.png`);
    }
  }
  console.log('Verified previews written. Inspect images and preservation_checks.json. Native Excel recalculation remains a separate check.');
}
