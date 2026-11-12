// Runs the REAL matching code of karafun_bridge.js (cut out of the file's own text, never copied) for tools/karafun_match_parity.py.
// Reads one JSON request per stdin line, answers one JSON line each. The page script itself is not loaded (it needs KaraFun's page).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'karafun_bridge.js'), 'utf8');
const a = src.indexOf('  const QUAL = ');
const b = src.indexOf('  const askedId = ');
if (a < 0 || b < 0 || b < a) throw new Error('matching block not found in karafun_bridge.js');
const block = src.slice(a, b);
const m = src.match(/const asked = \[query[^\n]*\n\s*if \(!item && asked\.length\) \{\n\s*const terms = \[\];\n([\s\S]*?)\n\s*for \(const term of terms\)/);
if (!m) throw new Error('term ladder not found in karafun_bridge.js');
const askedLine = src.match(/const asked = \[query[^\n]*/)[0];
const ladder = new Function('query', 'extra', `${askedLine}\n const terms = [];\n${m[1]}\n return terms;`);
const lib = new Function(`${block}\n return { words, titleKey, artistMatches, choose };`)();
const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', line => {
  if (!line.trim()) return;
  const r = JSON.parse(line);
  let v;
  if (r.op === 'words') v = lib.words(r.t);
  else if (r.op === 'title_key') v = lib.titleKey(r.t);
  else if (r.op === 'artist_matches') v = lib.artistMatches(r.have, r.want);
  else if (r.op === 'terms') v = ladder(r.queries[0], { queries: r.queries.slice(1) });
  else if (r.op === 'choose') {
    const list = r.list.map(x => ({ content: { id: x.id, title: x.title, artist: { name: x.artist } } }));
    const item = lib.choose(list, r.id, r.want_artist, r.want_title, r.query);
    v = item ? list.indexOf(item) : -1;
  }
  console.log(JSON.stringify({ v }));
});
