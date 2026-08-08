// canval page tests. Self-contained: a thirty-line DOM stand-in and
// inline fixtures, so this runs anywhere node runs, with nothing
// installed. It exists because every behaviour below was once verified
// by hand in a chat session -- and a check that lives in a chat session
// protects nothing.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "docs", "index.html"), "utf8");

let js = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace('"use strict";', "").replace(/^paint\(\);$/m, "").replace(/^load\(\);$/m, "");

const store = {};
class El {
  constructor(id){ Object.assign(this, {id, value:"", _h:"", textContent:"",
    disabled:false, hidden:false, placeholder:"", dataset:{}, handlers:{}, attrs:{}}); }
  set innerHTML(v){ this._h = v } get innerHTML(){ return this._h }
  addEventListener(k,f){ (this.handlers[k] ??= []).push(f) }
  fire(k,e={}){ (this.handlers[k]||[]).forEach(f => f({preventDefault(){}, ...e})) }
  setAttribute(k,v){ this.attrs[k]=v } getAttribute(k){ return this.attrs[k] }
  querySelectorAll(){ return [] } querySelector(){ return null }
  insertAdjacentHTML(){} focus(){} contains(){ return false }
  scrollIntoView(){} appendChild(){} remove(){} click(){ this.fire("click") }
}
global.document = { documentElement:{dataset:{}, lang:"", dir:""},
  body:{appendChild(){}}, getElementById: id => store[id] ??= new El(id),
  querySelectorAll: () => [], addEventListener(){}, createElement: () => new El("x") };
global.localStorage = { _d:{}, getItem(k){ return this._d[k] ?? null },
  setItem(k,v){ this._d[k]=v } };
global.matchMedia = () => ({matches:false});
global.location = { href: "https://example.test/" };
try{ global.navigator = {}; }catch{ /* newer node pins it; the real one works fine */ }
global.fetch = async name => ({ ok:true, json: async () => fixtures[name.replace("data/","")] });

const fixtures = {
  "vehicles.json": [
    { i:1, n:"ACME TRUCK", mk:"ACME", md:"TRUCK", y0:2019, y1:null, b:1,
      f:6, a:0, c:2, s:[{n:"Fuel Level"},{n:"RPM"}],
      lv:{ n:6, k:3, r:2, t:1754500000,
           s:[{n:"Fuel Level", v:"41", u:"%", t:1754500000},
              {n:"RPM", t:0}] } },
    { i:2, n:"ACME TRUCK", mk:"ACME", md:"TRUCK", y0:2024, y1:null, b:1,
      f:2, a:0, c:1, s:[{n:"Fuel Level"}], lv:{ n:2, k:2, r:0, s:[] } },
    { i:3, n:"J1939 GENERIC", mk:null, md:null, g:1, y0:null, y1:null, b:1,
      f:50, a:0, c:3, s:[], lv:{ n:6, k:2, r:1, s:[{n:"Odometer", v:"88", t:1}] } },
  ],
  "config.json": [ { i:9, nm:"ACME TRUCK 2019", hw:98 } ],
  "fleet.json": { estate:[["XtCAN 2G", 100]],
    top:[ {name:"ACME TRUCK", mk:"ACME", md:"TRUCK", y0:2019, y1:null, devices:8},
          {name:"J1939 GENERIC", g:1, y0:null, y1:null, devices:50} ] },
  "meta.json": { generated:"2026-08-08T01:00:00Z", devices:58, fitted:58, files:3 },
};

js = js.replace('const cMd = combo("md", {items: () => state.models, pick: v => {',
  'let cMdPick; const cMd = combo("md", {items: () => state.models, pick: cMdPick = v => {');

let failed = 0;
const check = (name, got, want) => {
  const ok = got === want;
  if(!ok) failed++;
  console.log(`${ok ? "  ok " : "FAIL"}  ${name}  ${ok ? "" : `(got ${JSON.stringify(got)}, wanted ${JSON.stringify(want)})`}`);
};

const run = `
(async () => {
  setTheme("light"); await load();

  // -- verdict ladder ------------------------------------------------
  const v1 = verdict(state.v.filter(x => x.i === 1), null);
  check("proven install is green",            v1.cls, "ok");
  check("green carries a sentence to say",    !!v1.say, true);
  check("sample spelled out",                 v1.nums[1][0], "2 / 3");

  const v2 = verdict(state.v.filter(x => x.i === 2), null);
  check("sampled but silent is red",          v2.cls, "bad");

  const v0 = verdict([{i:9, n:"X", mk:"X", md:"X", f:0, c:1, s:[], lv:{n:0,k:0,r:0,s:[]}}], null);
  check("file with no installs is red",       v0.cls, "bad");
  check("never-fitted has its own words",     v0.h !== v2.h, true);

  // -- activity unknown vs zero --------------------------------------
  check("no activity data anywhere",          actKnown(), false);
  check("file row shows a dash, not 0",       fileRow(state.v[0]).includes("\\u2014"), true);

  // -- year split ----------------------------------------------------
  const both = state.v.filter(x => x.mk === "ACME");
  const [b24, w24] = splitByFit(both, 2024);
  check("2024 exact file leads",   b24.every(f => f.y0 === 2024), true);
  check("2019 file becomes wider", w24.every(f => f.y0 === 2019), true);
  const cover20 = both.filter(f => coverYear(f, 2020));
  check("2020 falls to wide file", splitByFit(cover20, 2020)[0].length, 1);

  // -- cards step aside, and only for picker answers -----------------
  state.pick = {mk:"ACME"}; state.models = modelsOf("ACME");
  cMdPick("TRUCK");
  check("picker answer hides the cards",      store["cards"].hidden, true);
  clearAll();
  check("clear brings the cards back",        store["cards"].hidden, false);
  store["cards"].fire("click", {target:{closest: s => s === ".card" ? {dataset:{n:"0"}} : null}});
  check("card answer keeps them visible",     store["cards"].hidden, false);
  check("open card is marked",                store["cards"].innerHTML.includes('card on'), true);
  store["cards"].fire("click", {target:{closest: s => s === ".card" ? {dataset:{n:"0"}} : null}});
  check("second tap closes",                  state.cardOn, null);

  // -- share text carries the whole answer ---------------------------
  state.pick = {mk:"ACME"}; state.models = modelsOf("ACME"); cMdPick("TRUCK");
  check("share text names the vehicle",       (state.shareText||"").includes("ACME TRUCK"), true);
  check("share text carries the link",        (state.shareText||"").includes("https://"), true);

  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("RUNTIME ERROR:", e.stack); process.exit(1); });
`;
eval(js + run);
