/* ============================================================================
 * CANVAL XDM capture — paste once into the console on xdm.xgfleet.eu
 *
 * After pasting, just browse. It watches for the three calls that matter,
 * strips credentials, and downloads a file on its own once it has them.
 *
 *   Settings -> CAN files      gives the catalogue
 *   Devices -> click a device  gives device detail and hardware list
 *
 * Commands, if you want them:
 *   CANVAL.status()   what has been found so far
 *   CANVAL.save()     download now with whatever is captured
 *   CANVAL.stop()     restore the page
 * ==========================================================================*/

(function () {
  'use strict';

  if (window.CANVAL && window.CANVAL.stop) window.CANVAL.stop();

  // What we are here for. First match of each wins; later ones are ignored
  // so a busy session does not bloat the download.
  const TARGETS = [
    { id: 'canfiles',  match: /GetCanFilesWithAllParameters/i,
      label: 'CAN file catalogue',   hint: 'open Settings -> CAN files' },
    { id: 'device',    match: /GetItemWithAllParameters/i,
      label: 'device detail',        hint: 'open Devices and click one row' },
    { id: 'hardware',  match: /GetPossibleHardwareVersionsSdk/i,
      label: 'hardware versions',    hint: 'open Devices' },
  ];

  const found = {};
  const MAX_BODY = 6_000_000;
  let autoSaved = false;

  // ------------------------------------------------------------ redaction

  const SECRET_HEADERS = [
    'authorization', 'cookie', 'set-cookie', 'x-api-key', 'x-auth-token',
    'x-access-token', 'x-csrf-token', 'proxy-authorization',
  ];
  const SECRET_KEYS = /^(password|passwd|pwd|token|access_token|refresh_token|id_token|secret|client_secret|api_key|apikey|auth|authorization|session|sessionid|sid|jwt|signature)$/i;
  const SECRET_IN_URL = /([?&](?:token|access_token|auth|key|apikey|api_key|sid|session|jwt)=)[^&#]*/gi;

  const redactUrl = (u) => String(u).replace(SECRET_IN_URL, '$1XXXX');

  function redactHeaders(o) {
    const out = {};
    for (const k of Object.keys(o || {})) {
      out[k] = SECRET_HEADERS.includes(k.toLowerCase()) ? 'XXXX' : o[k];
    }
    return out;
  }

  function redactDeep(v, d) {
    d = d || 0;
    if (d > 12 || v === null || typeof v !== 'object') return v;
    if (Array.isArray(v)) return v.map((x) => redactDeep(x, d + 1));
    const out = {};
    for (const k of Object.keys(v)) {
      out[k] = SECRET_KEYS.test(k) ? 'XXXX' : redactDeep(v[k], d + 1);
    }
    return out;
  }

  function redactBody(text) {
    if (!text) return text;
    let s = String(text);
    if (s.length > MAX_BODY) s = s.slice(0, MAX_BODY) + '\n...TRUNCATED...';
    try {
      return JSON.stringify(redactDeep(JSON.parse(s)));
    } catch (e) {
      return s.replace(
        /("?(?:token|password|secret|authorization|jwt)"?\s*[:=]\s*"?)[^",&\s}]+/gi,
        '$1XXXX'
      );
    }
  }

  // -------------------------------------------------------------- capture

  function consider(entry) {
    const target = TARGETS.find((t) => t.match.test(entry.url) && !found[t.id]);
    if (!target) return;

    found[target.id] = entry;
    console.log(
      `%c  captured ${target.label}  (${Math.round(entry.responseBody.length / 1024)} KB)`,
      'color:#080;font-weight:bold'
    );
    report();

    if (Object.keys(found).length === TARGETS.length && !autoSaved) {
      autoSaved = true;
      console.log('%cAll three captured. Downloading ...', 'color:#080;font-weight:bold');
      setTimeout(() => window.CANVAL.save(), 400);
    }
  }

  function report() {
    console.log('  still needed:');
    let any = false;
    for (const t of TARGETS) {
      if (!found[t.id]) {
        any = true;
        console.log(`     ${t.label}  ->  ${t.hint}`);
      }
    }
    if (!any) console.log('     nothing, all set');
  }

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = (init && init.method) || (input && input.method) || 'GET';
    if (!TARGETS.some((t) => t.match.test(url))) {
      return origFetch.apply(this, arguments);
    }

    const headers = {};
    try {
      new Headers((init && init.headers) || (input && input.headers) || {})
        .forEach((v, k) => (headers[k] = v));
    } catch (e) { /* ignore */ }
    const reqBody = init && init.body ? String(init.body) : null;

    return origFetch.apply(this, arguments).then((resp) => {
      resp.clone().text().then((text) => consider({
        kind: 'fetch', method, url: redactUrl(url), status: resp.status,
        requestHeaders: redactHeaders(headers),
        requestBody: redactBody(reqBody),
        responseBody: redactBody(text) || '',
      })).catch(() => {});
      return resp;
    });
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__cv = { method, url, headers: {} };
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (this.__cv) this.__cv.headers[k] = v;
    return origSetHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__cv;
    if (meta && TARGETS.some((t) => t.match.test(meta.url))) {
      this.addEventListener('load', function () {
        let text = '';
        try {
          text = (this.responseType === '' || this.responseType === 'text')
            ? this.responseText
            : JSON.stringify(this.response);
        } catch (e) { /* ignore */ }
        consider({
          kind: 'xhr', method: meta.method, url: redactUrl(meta.url),
          status: this.status, requestHeaders: redactHeaders(meta.headers),
          requestBody: redactBody(body ? String(body) : null),
          responseBody: redactBody(text) || '',
        });
      });
    }
    return origSend.apply(this, arguments);
  };

  // -------------------------------------------------------------- commands

  window.CANVAL = {
    found,

    status() {
      console.log('%cCaptured so far:', 'font-weight:bold');
      for (const t of TARGETS) {
        const e = found[t.id];
        console.log(e
          ? `  OK       ${t.label}  (${Math.round(e.responseBody.length / 1024)} KB)`
          : `  missing  ${t.label}  ->  ${t.hint}`);
      }
      return Object.keys(found);
    },

    save() {
      const calls = TARGETS.filter((t) => found[t.id])
                           .map((t) => ({ target: t.id, ...found[t.id] }));
      if (!calls.length) {
        console.warn('Nothing captured yet. Browse to the pages listed above.');
        return;
      }
      const blob = new Blob(
        [JSON.stringify({ captured: new Date().toISOString(), calls }, null, 2)],
        { type: 'application/json' }
      );
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'canval-xdm.json';
      a.click();
      URL.revokeObjectURL(a.href);
      console.log(`%cSaved ${calls.length} call(s) to canval-xdm.json`,
                  'color:#080;font-weight:bold');
      console.log('%cSkim it before sending. Credentials are stripped, but the '
                + 'body still holds your own fleet data.', 'color:#a60');
    },

    stop() {
      window.fetch = origFetch;
      XMLHttpRequest.prototype.open = origOpen;
      XMLHttpRequest.prototype.send = origSend;
      XMLHttpRequest.prototype.setRequestHeader = origSetHeader;
      console.log('Capture stopped, page restored.');
    },
  };

  console.log('%cCANVAL XDM capture is running.',
              'color:#080;font-weight:bold;font-size:14px');
  console.log('Just browse — it saves on its own once it has all three.\n');
  report();
})();
