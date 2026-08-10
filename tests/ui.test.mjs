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
      f:6, a:0, c:2, s:["Fuel Level","RPM"], x:["Trunk Open"],
      sc:{n:5, s:["Lock_Unlock_Outputs_Registration Unified V2_1"]},
      lv:{ n:6, k:3, r:2, t:1754500000,
           s:[{n:"Fuel Level", v:"41", u:"%", t:1754500000},
              {n:"RPM", t:0}],
           d:[{i:"860000000000001", u:"TRUCK-1", o:"acme_co", cb:"someone",
               t:1754500000, r:[{n:"Fuel Level", v:"41"}, {n:"RPM"}]}] } },
    { i:2, n:"ACME TRUCK", mk:"ACME", md:"TRUCK", y0:2024, y1:null, b:1,
      f:2, a:0, c:1, s:["Fuel Level"], lv:{ n:2, k:2, r:0, s:[] } },
    { i:3, n:"J1939 GENERIC", mk:null, md:null, g:1, y0:null, y1:null, b:1,
      f:50, a:0, c:3, s:[], lv:{ n:6, k:2, r:1, s:[{n:"Odometer", v:"88", t:1}] } },
  ],
  "configs.json": [ { i:9, nm:"ACME TRUCK 2019", hw:98 } ],
  // A vehicle with no file of its own, fitted on somebody else's.
  "crossfits.json": [
    { nm:"Beta-Wagon_2026-Upgraded XTCAN2G", fl:"ACME TRUCK", i:1, n:25, a:3 },
  ],
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

  // -- catalogue promise only where nothing is proven ----------------
  const proven   = detail(state.v.find(x => x.i === 1));   // has live readings
  const unproven = detail(state.v.find(x => x.i === 2));   // sampled, silent
  check("proven file folds the promise",  proven.includes('class="dtog"'), true);
  check("proven file hides the chips",    proven.includes('class="decl" hidden'), true);
  check("proven file still shows devices", proven.includes("860000000000001"), true);
  check("unproven file shows the promise", unproven.includes('class="dtog"'), false);
  check("unproven promise is not hidden",  unproven.includes('class="decl" hidden'), false);
  check("unproven names its signals",      unproven.includes("Fuel Level"), true);

  // -- share text carries the whole answer ---------------------------
  state.pick = {mk:"ACME"}; state.models = modelsOf("ACME"); cMdPick("TRUCK");
  check("share text names the vehicle",       (state.shareText||"").includes("ACME TRUCK"), true);
  check("share text carries the link",        (state.shareText||"").includes("https://"), true);

  // -- car sharing rides on a proven lock script, or stays silent -----
  check("verdict carries the lock proof",   verdict(state.v.filter(x => x.i === 1), null).cs.n, 5);
  check("no script, no claim",              !verdict(state.v.filter(x => x.i === 2), null).cs, true);
  check("answer shows the car-share line",  store["out"].innerHTML.includes('say cshare'), true);
  check("the script is named, not implied", store["out"].innerHTML.includes("Lock_Unlock"), true);
  check("control sits with the sensors",    store["out"].innerHTML.includes('class="s cap"'), true);
  check("the chip says it is control",      store["out"].innerHTML.includes(T("capLock")), true);
  check("the note covers control too",      store["out"].innerHTML.includes(T("liveNoteC")), true);
  check("share text carries the lock line", (state.shareText||"").includes(T("csShare",{n:5})), true);

  // -- every make has a face, every answer a look link ----------------
  // -- Arabic typing reaches a Latin catalogue -----------------------
  check("Arabic make finds the Latin one", !!qhit("KIA", "كيا"), true);
  check("half a word is enough",           !!qhit("CERATO", "سير"), true);
  check("Latin still works lowercase",     !!qhit("KIA", "kia"), true);
  check("a wrong make still misses",       qhit("KIA", "بيجو"), null);
  check("empty query shows everything",    qhit("KIA", ""), "");
  check("the highlight is the translation", qhit("KIA", "كيا"), "kia");

  // -- fitting photos, with a store standing in for the browser's ----
  const mem = [];
  SHOTS.ready     = () => true;
  SHOTS.needsAuth = () => false;
  SHOTS.list      = async () => mem.slice().sort((a, b) => b.at - a.at);
  SHOTS.del       = async id => { const i = mem.findIndex(x => x.id === id);
                                  if(i >= 0) mem.splice(i, 1); };
  check("one car, one key", shotCar("KIA ", " cerato"), "kia|cerato");
  await mountShots("ACME", "TRUCK", 2024);
  check("nothing yet, and it says so", store["shgrid"].innerHTML.includes(T("shEmpty")), true);

  mem.push({id:"a", car:shotCar("ACME","TRUCK"), yr:2019, at:2, src:"data:,x", note:"OBD pin 6"});
  mem.push({id:"b", car:shotCar("ACME","TRUCK"), yr:2024, at:1, src:"data:,y", note:""});
  await mountShots("ACME", "TRUCK", 2024);
  const g = store["shgrid"].innerHTML;
  check("the year asked for leads",    g.indexOf('data-hit="1"') < g.indexOf("OBD pin 6"), true);
  check("other years are kept",        g.includes("2019"), true);
  check("the note rides with it",      g.includes("OBD pin 6"), true);
  check("the count is on the header",  store["shn"].textContent, "2");
  check("the note has a box to live in", g.includes('class="shnote"'), true);

  // The note is why the photo is worth keeping, so prove it survives.
  let saved = null;
  SHOTS.setNote = async r => { saved = r; return r; };
  const back = await saveNote(mem, "b", "  CAN on OBD pins 6 and 14  ");
  check("the note is written through", saved && saved.id, "b");
  check("and trimmed on the way",      back.note, "CAN on OBD pins 6 and 14");
  check("a note for nothing saves nothing", await saveNote(mem, "zz", "x"), null);

  await SHOTS.del("a");
  await mountShots("ACME", "TRUCK", 2024);
  check("removing one leaves the rest", store["shn"].textContent, "1");
  check("the note came back with it",   store["shgrid"].innerHTML.includes("OBD pins 6 and 14"), true);

  // Signed out is a state with a door in it, not an empty shelf.
  SHOTS.needsAuth = () => true;
  await mountShots("ACME", "TRUCK", 2024);
  check("signed out shows the door",  store["shgrid"].innerHTML.includes('id="shsign"'), true);
  check("and says why it is there",   store["shgrid"].innerHTML.includes(T("shSignWhy")), true);
  SHOTS.needsAuth = () => false;

  // -- fitted under another vehicle's file ---------------------------
  check("the crossing is found",   crossFor("BETA", "WAGON").length, 1);
  check("and carries the file",    crossFor("BETA", "WAGON")[0].fl, "ACME TRUCK");
  check("a stranger finds none",   crossFor("ACME", "TRUCK").length, 0);

  check("slug handles spaced names",  logoSlug("LAND ROVER"), "land-rover");
  check("cards carry a logo cell",    store["cards"].innerHTML.includes('class="lg'), true);
  check("cards carry the fallback",   store["cards"].innerHTML.includes('class="mono"'), true);
  check("answer header shows the make", store["out"].innerHTML.includes("logos/acme.png"), true);
  check("look link targets the pick", store["out"].innerHTML.includes("q=ACME%20TRUCK"), true);
  check("make rows are marked for logos", !!(state.makes[0] && state.makes[0].logo), true);
  check("card logo sits opposite the text", /card-lg\{[^}]*inset-inline-end/.test(html), true);

  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("RUNTIME ERROR:", e.stack); process.exit(1); });
`;
eval(js + run);
