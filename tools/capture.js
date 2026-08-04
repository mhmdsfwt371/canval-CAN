/* ============================================================================
 * CANVAL capture — paste into the browser console on the tracking platform.
 *
 * Records network traffic (fetch, XHR and WebSocket), strips credentials
 * before anything leaves the page, and tells you straight away whether the
 * per-parameter timestamps are in the payload.
 *
 * Commands, after pasting:
 *     CANVAL.list()        what was captured
 *     CANVAL.best()        the call carrying sensor data, analysed
 *     CANVAL.stamps(n)     what timestamp fields call n contains
 *     CANVAL.save()        download a redacted JSON file
 *     CANVAL.show(n)       print one call in full
 *     CANVAL.stop()        restore the page to normal
 *
 * Nothing is uploaded anywhere. Everything stays in the tab until you
 * choose to save it.
 * ==========================================================================*/

(function () {
  'use strict';

  if (window.CANVAL && window.CANVAL.stop) {
    window.CANVAL.stop();
  }

  const calls = [];
  const MAX_BODY = 2_000_000;

  // ------------------------------------------------------------ redaction

  const SECRET_HEADERS = [
    'authorization', 'cookie', 'set-cookie', 'x-api-key', 'x-auth-token',
    'x-access-token', 'x-csrf-token', 'proxy-authorization', 'x-session-id',
  ];

  const SECRET_KEYS = /^(password|passwd|pwd|token|access_token|refresh_token|id_token|secret|client_secret|api_key|apikey|auth|authorization|session|sessionid|sid|jwt|hash|signature)$/i;

  const SECRET_IN_URL = /([?&](?:token|access_token|auth|key|apikey|api_key|sid|session|password|jwt|hash)=)[^&#]*/gi;

  function redactUrl(url) {
    try {
      return String(url).replace(SECRET_IN_URL, '$1XXXX');
    } catch (e) {
      return String(url);
    }
  }

  function redactHeaders(obj) {
    const out = {};
    for (const k of Object.keys(obj || {})) {
      out[k] = SECRET_HEADERS.includes(k.toLowerCase()) ? 'XXXX' : obj[k];
    }
    return out;
  }

  // Walk a parsed body and blank anything that looks like a credential.
  function redactDeep(value, depth) {
    depth = depth || 0;
    if (depth > 12 || value === null || typeof value !== 'object') return value;
    if (Array.isArray(value)) return value.map((v) => redactDeep(v, depth + 1));

    const out = {};
    for (const k of Object.keys(value)) {
      out[k] = SECRET_KEYS.test(k) ? 'XXXX' : redactDeep(value[k], depth + 1);
    }
    return out;
  }

  function redactBody(text) {
    if (!text) return text;
    let body = String(text);
    if (body.length > MAX_BODY) body = body.slice(0, MAX_BODY) + '\n...TRUNCATED...';
    try {
      return JSON.stringify(redactDeep(JSON.parse(body)));
    } catch (e) {
      // not JSON: fall back to a blunt pattern wipe
      return body.replace(
        /("?(?:token|password|secret|authorization|jwt)"?\s*[:=]\s*"?)[^",&\s}]+/gi,
        '$1XXXX'
      );
    }
  }

  function record(entry) {
    entry.id = calls.length;
    entry.at = new Date().toISOString();
    calls.push(entry);
  }

  // MQTT over WebSocket sends binary frames, not strings. Decoding them as
  // UTF-8 leaves a few protocol control bytes at the front and readable
  // JSON after that, which is all the analysis needs.
  const stats = { frames: 0, binary: 0, text: 0, undecodable: 0 };

  function toText(data) {
    if (typeof data === 'string') { stats.text++; return data; }
    try {
      if (data instanceof ArrayBuffer) {
        stats.binary++;
        return new TextDecoder('utf-8', { fatal: false }).decode(data);
      }
      if (ArrayBuffer.isView(data)) {
        stats.binary++;
        return new TextDecoder('utf-8', { fatal: false }).decode(data.buffer);
      }
    } catch (e) { /* fall through */ }
    stats.undecodable++;
    return null;                        // Blob is handled asynchronously
  }

  function recordFrame(data, url, kind) {
    stats.frames++;

    const emit = (text) => {
      if (text === null || text === undefined) return;
      record({
        kind, method: 'MESSAGE', url: redactUrl(url), status: 101,
        requestHeaders: {}, requestBody: null,
        responseBody: redactBody(text),
      });
    };

    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      stats.binary++;
      data.text ? data.text().then(emit).catch(() => {}) : null;
      return;
    }
    emit(toText(data));
  }

  // -------------------------------------------------------------- capture

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method =
      (init && init.method) || (input && input.method) || 'GET';
    const reqHeaders = {};
    try {
      new Headers((init && init.headers) || (input && input.headers) || {})
        .forEach((v, k) => (reqHeaders[k] = v));
    } catch (e) { /* ignore */ }

    const reqBody = init && init.body ? String(init.body) : null;

    return origFetch.apply(this, arguments).then((resp) => {
      resp
        .clone()
        .text()
        .then((text) =>
          record({
            kind: 'fetch',
            method,
            url: redactUrl(url),
            status: resp.status,
            requestHeaders: redactHeaders(reqHeaders),
            requestBody: redactBody(reqBody),
            responseBody: redactBody(text),
          })
        )
        .catch(() => {});
      return resp;
    });
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__canval = { method, url, headers: {} };
    return origOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (this.__canval) this.__canval.headers[k] = v;
    return origSetHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__canval;
    if (meta) {
      this.addEventListener('load', function () {
        let text = '';
        try {
          text =
            this.responseType === '' || this.responseType === 'text'
              ? this.responseText
              : JSON.stringify(this.response);
        } catch (e) { /* ignore */ }

        record({
          kind: 'xhr',
          method: meta.method,
          url: redactUrl(meta.url),
          status: this.status,
          requestHeaders: redactHeaders(meta.headers),
          requestBody: redactBody(body ? String(body) : null),
          responseBody: redactBody(text),
        });
      });
    }
    return origSend.apply(this, arguments);
  };

  // Live dashboards often push updates over a socket instead of polling.
  // If nothing shows up under fetch/xhr, this is where it will be.
  const origWS = window.WebSocket;
  if (typeof origWS === 'function') {
    try {
      const PatchedWS = function (url, protocols) {
        const ws = protocols ? new origWS(url, protocols) : new origWS(url);
        ws.addEventListener('message', function (ev) {
          recordFrame(ev.data, url, 'websocket');
        });
        return ws;
      };
      PatchedWS.prototype = origWS.prototype;
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(
        (k) => (PatchedWS[k] = origWS[k])
      );
      window.WebSocket = PatchedWS;
    } catch (e) {
      console.warn('WebSocket capture unavailable, continuing without it:', e);
    }
  }

  // Server-sent events are the other common push channel.
  const origES = window.EventSource;
  if (typeof origES === 'function') {
    try {
      const PatchedES = function (url, cfg) {
        const es = cfg ? new origES(url, cfg) : new origES(url);
        es.addEventListener('message', function (ev) {
          recordFrame(ev.data, url, 'eventsource');
        });
        return es;
      };
      PatchedES.prototype = origES.prototype;
      window.EventSource = PatchedES;
    } catch (e) { /* ignore */ }
  }

  // --------------------------------------------------- already-open sockets
  //
  // Patching the constructor only catches connections opened afterwards.
  // A live dashboard has usually connected long before this script is
  // pasted, so hunt down the existing instance and listen in on it.

  function hookLiveSockets() {
    const seen = new Set();
    const hooked = [];

    function attach(ws, where) {
      if (!ws || ws.__canvalHooked) return;
      try {
        ws.addEventListener('message', function (ev) {
          recordFrame(ev.data, ws.url || where, 'websocket(live)');
        });
        ws.__canvalHooked = true;
        hooked.push(where + '  ->  ' + (ws.url || '?'));
      } catch (e) { /* ignore */ }
    }

    let budget = 20000;                 // keep the scan from stalling the tab

    function scan(obj, where, depth) {
      if (!obj || depth > 6 || budget <= 0 || typeof obj !== 'object') return;
      let keys = [];
      try { keys = Object.keys(obj); } catch (e) { return; }

      for (const k of keys.slice(0, 400)) {
        if (budget-- <= 0) return;
        let v;
        try { v = obj[k]; } catch (e) { continue; }   // guard hostile getters
        if (!v || typeof v !== 'object' || seen.has(v)) continue;
        seen.add(v);

        const isWS =
          (typeof origWS === 'function' && v instanceof origWS) ||
          (typeof v.url === 'string' &&
            typeof v.readyState === 'number' &&
            typeof v.send === 'function');

        if (isWS) attach(v, where + '.' + k);
        else scan(v, where + '.' + k, depth + 1);
      }
    }

    try { scan(window, 'window', 0); } catch (e) { /* ignore */ }

    // socket.io keeps its connections in a registry
    try {
      if (window.io && window.io.managers) {
        for (const key of Object.keys(window.io.managers)) {
          const eng = window.io.managers[key].engine;
          if (eng && eng.transport && eng.transport.ws) {
            attach(eng.transport.ws, 'io.managers');
          }
        }
      }
    } catch (e) { /* ignore */ }

    return hooked;
  }

  // -------------------------------------------------------------- analysis

  // Signals that a payload is the one we are after.
  const SENSOR_HINT = /sensor_\d+|"ePwrV"|parameterKey|paramName|sensorType/i;

  function scoreCall(c) {
    const body = c.responseBody || '';
    let score = 0;
    const hits = body.match(/sensor_\d+/g);
    if (hits) score += new Set(hits).size * 10;
    if (SENSOR_HINT.test(body)) score += 5;
    if (/16385|16386|16387|12300|12301|8200|8219/.test(body)) score += 20;
    return score;
  }

  // A number that could plausibly be a unix time, or an ISO date string.
  function looksLikeTime(v) {
    if (typeof v === 'number') {
      if (v > 1_500_000_000 && v < 2_500_000_000) return 'unix seconds';
      if (v > 1_500_000_000_000 && v < 2_500_000_000_000) return 'unix millis';
    }
    if (typeof v === 'string') {
      if (/^\d{10}$/.test(v)) return 'unix seconds (string)';
      if (/^\d{13}$/.test(v)) return 'unix millis (string)';
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v)) return 'ISO date';
    }
    return null;
  }

  // An MQTT frame carries a few protocol bytes before the payload, so a
  // plain JSON.parse fails on the whole thing. Find where the real object
  // starts and parse from there.
  function looseParse(text) {
    if (!text) return null;
    try { return JSON.parse(text); } catch (e) { /* keep going */ }

    for (const open of ['{', '[']) {
      let i = text.indexOf(open);
      while (i !== -1) {
        const close = open === '{' ? '}' : ']';
        const end = text.lastIndexOf(close);
        if (end > i) {
          try { return JSON.parse(text.slice(i, end + 1)); } catch (e) { /* next */ }
        }
        i = text.indexOf(open, i + 1);
        if (i > 4096) break;            // payload should start near the front
      }
    }
    return null;
  }

  function findTimeFields(obj, path, found, depth) {
    path = path || '$'; found = found || {}; depth = depth || 0;
    if (depth > 14 || obj === null || typeof obj !== 'object') return found;

    if (Array.isArray(obj)) {
      obj.slice(0, 3).forEach((v, i) =>
        findTimeFields(v, path + '[' + i + ']', found, depth + 1)
      );
      return found;
    }

    for (const k of Object.keys(obj)) {
      const v = obj[k];
      const kind = looksLikeTime(v);
      if (kind) {
        const key = path + '.' + k;
        if (!found[key]) found[key] = { field: k, format: kind, sample: v };
      } else if (v && typeof v === 'object') {
        findTimeFields(v, path + '.' + k, found, depth + 1);
      }
    }
    return found;
  }

  function relevant() {
    return calls
      .map((c) => ({ call: c, score: scoreCall(c) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  // -------------------------------------------------------------- commands

  window.CANVAL = {
    calls,

    list() {
      if (!calls.length) {
        console.log('Nothing captured yet. Open the device so its parameters load.');
        return;
      }
      console.table(
        calls.map((c) => ({
          id: c.id,
          kind: c.kind,
          method: c.method,
          status: c.status,
          score: scoreCall(c),
          bytes: (c.responseBody || '').length,
          url: c.url.length > 90 ? c.url.slice(0, 90) + '…' : c.url,
        }))
      );
      console.log('Run CANVAL.best() to analyse the highest scoring call.');
    },

    // Tells apart "nothing is arriving" from "things arrive but do not match".
    // Without this the empty result is ambiguous and we cannot debug it.
    probe() {
      console.log('%cCapture diagnostics', 'font-weight:bold;font-size:13px');
      console.table([{
        'socket frames seen': stats.frames,
        'text frames': stats.text,
        'binary frames': stats.binary,
        'undecodable': stats.undecodable,
        'calls recorded': calls.length,
        'calls with sensor data': relevant().length,
      }]);

      if (stats.frames === 0 && calls.length === 0) {
        console.warn(
          'Nothing at all is passing through this script.\n' +
          'The connection is open but out of reach -- bundled apps keep it\n' +
          'inside a closure, not on window.\n\n' +
          'FORCE A RECONNECT, this is the reliable fix:\n' +
          '  Network tab -> throttling dropdown -> Offline\n' +
          '  wait 3 seconds\n' +
          '  back to No throttling\n' +
          'The app rebuilds its connection through the patched constructor.\n' +
          'Then wait ~20s and run CANVAL.probe() again.'
        );
      } else if (stats.frames > 0 && !relevant().length) {
        console.warn(
          'Frames are arriving but none carry sensor keys.\n' +
          'Look at a raw one with CANVAL.show(0) and send Claude the first\n' +
          'few hundred characters -- the payload may be encoded or the\n' +
          'sensor values may travel on a different topic.'
        );
      }
      return stats;
    },

    hookLive() {
      const hooked = hookLiveSockets();
      if (hooked.length) {
        console.log(
          '%cAttached to ' + hooked.length + ' already-open socket(s):',
          'color:#080;font-weight:bold'
        );
        hooked.forEach((h) => console.log('   ' + h));
        console.log('Wait ~15 seconds for a push, then run CANVAL.best()');
      } else {
        console.warn(
          'No open socket found on window.\n' +
          'Try forcing a reconnect instead:\n' +
          '  Network tab -> throttling dropdown -> Offline\n' +
          '  wait 3 seconds -> back to No throttling\n' +
          'The app will rebuild its connection and this script will catch it.'
        );
      }
      return hooked;
    },

    best() {
      const hits = relevant();
      if (!hits.length) {
        console.warn(
          'No call contained sensor data yet.\n' +
          '  1. Run CANVAL.hookLive() -- the connection was probably already\n' +
          '     open before this script was pasted, so nothing passed through it.\n' +
          '  2. Close and reopen the device panel to force a fresh load.\n' +
          '  3. Still nothing? Network tab -> Offline for 3s -> No throttling,\n' +
          '     which makes the app reconnect through the patched constructor.\n' +
          '  4. Run CANVAL.probe() -- it will say whether anything is arriving.'
        );
        return;
      }
      const c = hits[0].call;
      console.log('%cBest match: call #' + c.id, 'font-weight:bold');
      console.log(c.method, c.url, '->', c.status, '(' + c.kind + ')');

      const keys = (c.responseBody.match(/sensor_\d+/g) || []);
      console.log('distinct sensor keys:', new Set(keys).size);

      this.stamps(c.id);
      console.log('Run CANVAL.save() to download it.');
      return c;
    },

    stamps(id) {
      const c = calls[id];
      if (!c) return console.warn('No call #' + id);
      const parsed = looseParse(c.responseBody);
      if (!parsed) {
        console.warn('Could not parse this payload. Inspect it with CANVAL.show(' + id + ')');
        return;
      }
      const found = findTimeFields(parsed);
      const rows = Object.keys(found).map((p) => ({
        path: p, field: found[p].field,
        format: found[p].format, sample: found[p].sample,
      }));

      if (!rows.length) {
        console.warn(
          '%cNO TIMESTAMPS IN THIS PAYLOAD.',
          'color:#c00;font-weight:bold'
        );
        console.warn(
          'The "10 seconds ago" text is then computed elsewhere, or comes ' +
          'from a second call. Check CANVAL.list() for another candidate ' +
          'and tell Claude — this changes the plan.'
        );
      } else {
        console.log('%cTimestamp fields found:', 'color:#080;font-weight:bold');
        console.table(rows);
      }
      return rows;
    },

    show(id) {
      const c = calls[id];
      if (!c) return console.warn('No call #' + id);
      console.log(JSON.stringify(c, null, 2));
      return c;
    },

    save(id) {
      const payload =
        id === undefined
          ? { captured: new Date().toISOString(), calls: relevant().map((x) => x.call) }
          : { captured: new Date().toISOString(), calls: [calls[id]] };

      if (!payload.calls.length || !payload.calls[0]) {
        console.warn('Nothing relevant to save. Run CANVAL.list() first.');
        return;
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
      });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'canval-capture.json';
      a.click();
      URL.revokeObjectURL(a.href);
      console.log('Saved ' + payload.calls.length + ' call(s) to canval-capture.json');
      console.log(
        '%cSkim the file before sending it. Credentials are redacted, but ' +
        'the payload still holds your own fleet data.',
        'color:#a60'
      );
    },

    stop() {
      window.fetch = origFetch;
      XMLHttpRequest.prototype.open = origOpen;
      XMLHttpRequest.prototype.send = origSend;
      XMLHttpRequest.prototype.setRequestHeader = origSetHeader;
      window.WebSocket = origWS;
      console.log('Capture stopped, page restored.');
    },
  };

  const preHooked = hookLiveSockets();

  console.log(
    '%cCANVAL capture is running.',
    'color:#080;font-weight:bold;font-size:14px'
  );
  if (preHooked.length) {
    console.log(
      '%cAttached to ' + preHooked.length + ' socket(s) that were already open.',
      'color:#080'
    );
    preHooked.forEach((h) => console.log('   ' + h));
  } else {
    console.log('No open socket found yet -- that is fine if the app uses plain requests.');
  }
  console.log('Now open device 869595063350010 and let its parameters load.');
  console.log('Wait ~20 seconds, then run:  CANVAL.probe()');
})();
