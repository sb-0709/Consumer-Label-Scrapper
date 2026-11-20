"""One-time website setup for an ISP: record how its address check works, then run it automatically.

    cls setup-isp --name "Believe Broadband" --url https://www.believebroadband.com/ --frn 0016095432

A normal Chrome window opens on the ISP's site with a small instruction bar on top. You do one
address lookup the way a customer would; the bar tells you what to click:

    1. (optional) the button that opens the address form
    2. the street-address box  (type the sample address the bar shows)
    3. (optional) the matching address in the dropdown
    4. the button that submits the address
    5. (optional) a "Broadband Facts" / label link on the plans page

The clicked elements are saved as selectors in isp_sites.yaml. From then on the ISP is checked
automatically for every address (same engine as AT&T / Verizon / Spectrum / Xfinity), and the
setup is tested straight away on other addresses that the FCC map says the ISP serves.
"""
from __future__ import annotations

import re
from datetime import date

import yaml

from .config import ROOT

SITES_FILE = ROOT / "isp_sites.yaml"

STEPS = [
    ("open_form", True, "If the street-address box is NOT visible yet, click the button/link that opens it "
                        "(e.g. 'Check availability'). Otherwise press Skip."),
    ("address_input", False, "Click the STREET ADDRESS box, then type: {address}"),
    ("suggestion", True, "If a dropdown of addresses appears, click the matching one. Otherwise press Skip."),
    ("submit", False, "Click the button that submits the address (e.g. 'Check availability', 'Go', 'See plans')."),
    ("label_links", True, "On the plans page, click a 'Broadband Facts' / 'View label' link for one plan. "
                          "If the labels are already shown (or there are none), press Skip."),
]

# Injected into every page: instruction bar + click recorder (capture phase; the click still happens).
OVERLAY_JS = r"""
(() => {
  if (window.__clsInstalled) return; window.__clsInstalled = true;
  const cands = (el) => {
    const out = [], t = el.tagName.toLowerCase(), q = (v) => v.replace(/"/g, '\\"');
    if (el.id && !/\d{4,}|[0-9a-f]{8,}|:|^ember|^react|^mui/i.test(el.id)) out.push('#' + CSS.escape(el.id));
    for (const a of ['name', 'data-testid', 'data-test', 'aria-label', 'placeholder', 'autocomplete', 'title']) {
      const v = el.getAttribute(a);
      if (v && v.length < 80 && !(a === 'autocomplete' && /^(off|on|nope|false)$/i.test(v))) out.push(`${t}[${a}="${q(v)}"]`);
    }
    const txt = ((el.innerText || el.value || '') + '').trim().split('\n')[0].slice(0, 40);
    if (txt && /^(button|a|input)$/.test(t)) out.push(`${t}:has-text("${q(txt)}")`);
    if (txt && el.getAttribute('role') === 'button') out.push(`[role="button"]:has-text("${q(txt)}")`);
    return [...new Set(out)];
  };
  const suggestion = (el) => {
    const opt = el.closest('[role="option"]'); if (opt) return ['[role="option"]'];
    const li = el.closest('li');
    if (li && li.parentElement) {
      const ul = li.parentElement, out = [];
      if (ul.id && !/\d{4,}/.test(ul.id)) out.push('#' + CSS.escape(ul.id) + ' li');
      if (ul.getAttribute('role')) out.push(`[role="${ul.getAttribute('role')}"] li`);
      for (const c of ul.classList) if (!/\d{3,}/.test(c)) out.push(`${ul.tagName.toLowerCase()}.${CSS.escape(c)} li`);
      return out.length ? out : ['li'];
    }
    return cands(el);
  };
  document.addEventListener('click', (ev) => {
    if (ev.target.closest('#__cls_bar')) return;
    const el = ev.target.closest('button, a, input, select, textarea, [role="option"], [role="button"], li') || ev.target;
    const text = ((el.innerText || el.value || '') + '').trim().slice(0, 80);
    window.clsPick({cands: cands(el), suggestion: suggestion(el), text, tag: el.tagName.toLowerCase(),
                    url: location.href});
  }, true);
  const render = (st) => {
    let bar = document.getElementById('__cls_bar');
    if (!bar) {
      bar = document.createElement('div'); bar.id = '__cls_bar';
      bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1f3a8a;color:#fff;' +
        'font:15px/1.4 -apple-system,Segoe UI,sans-serif;padding:10px 14px;display:flex;gap:12px;align-items:center;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.3)';
      (document.body || document.documentElement).appendChild(bar);
    }
    bar.innerHTML = '';
    const msg = document.createElement('div'); msg.style.flex = '1';
    msg.textContent = st.done ? '✅ Setup recorded. You can close this window.' : `Step ${st.n}/${st.total}: ${st.text}`;
    bar.appendChild(msg);
    if (!st.done) {
      for (const [label, action] of [['Skip', 'skip'], ['Finish', 'finish']]) {
        if (label === 'Skip' && !st.optional) continue;
        const b = document.createElement('button'); b.textContent = label;
        b.style.cssText = 'background:#fff;color:#1f3a8a;border:0;border-radius:6px;padding:6px 12px;font-weight:600;cursor:pointer';
        b.onclick = (e) => { e.stopPropagation(); window.clsAction(action).then(render); };
        bar.appendChild(b);
      }
    }
  };
  window.__clsRender = render;
  const refresh = () => window.clsState().then(render).catch(() => {});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh); else refresh();
  setInterval(refresh, 1500);
})();
"""


def _esc(text: str) -> str:
    return re.escape(re.sub(r"\s+", " ", text).strip()[:40])


def record(key: str, name: str, start_url: str, sample_address: str, *, frns: list[str] | None = None,
           patterns: str = "", timeout_minutes: float = 15, log=print, _drive=None, _headless: bool = False) -> dict:
    """Open the ISP's site, record the address-check steps, save them to isp_sites.yaml."""
    from playwright.sync_api import sync_playwright

    state = {"i": 0, "done": False, "picked": {}}

    def current() -> dict:
        if state["done"] or state["i"] >= len(STEPS):
            return {"done": True}
        step, optional, text = STEPS[state["i"]]
        return {"done": False, "n": state["i"] + 1, "total": len(STEPS), "optional": optional,
                "text": text.format(address=sample_address)}

    def advance():
        state["i"] += 1
        if state["i"] >= len(STEPS):
            state["done"] = True

    def on_pick(source, data):
        if state["done"]:
            return
        step = STEPS[state["i"]][0]
        if step == "address_input":
            if data["tag"] not in ("input", "textarea"):
                return                                  # wait for a click on an actual text box
            state["picked"][step] = data["cands"]
        elif step == "suggestion":
            state["picked"][step] = data["suggestion"]
        elif step == "label_links":
            if data["text"]:
                state["picked"][step] = [_esc(data["text"])]
        else:
            state["picked"][step] = data["cands"]
        log(f"  recorded {step}: {state['picked'].get(step)}")
        advance()

    def on_action(source, action):
        if action == "skip" and not state["done"]:
            advance()
        elif action == "finish":
            state["done"] = True
        return current()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=_headless, channel="chrome")
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US")
        ctx.expose_binding("clsPick", on_pick)
        ctx.expose_binding("clsAction", on_action)
        ctx.expose_binding("clsState", lambda source: current())
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
        if _drive:                                     # tests: script the clicks a person would make
            _drive(page)
        deadline = timeout_minutes * 60 * 1000
        waited = 0
        try:
            while not state["done"] and waited < deadline and browser.is_connected() and ctx.pages:
                ctx.pages[-1].wait_for_timeout(500)
                waited += 500
            if state["done"] and ctx.pages:
                ctx.pages[-1].wait_for_timeout(1500)   # let the "recorded" message show
        except Exception:  # noqa: BLE001 - window closed by the user
            pass
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass

    picked = state["picked"]
    if "address_input" not in picked or "submit" not in picked:
        raise RuntimeError("Setup incomplete: the address box and the submit button are needed. Run it again.")
    site = {
        "name": name, "start_url": start_url, "frns": [str(f).zfill(10) for f in (frns or [])],
        "patterns": patterns or re.escape(name.split(" (")[0]),
        **{k: v for k, v in picked.items() if v},
        "configured": date.today().isoformat(),
    }
    save_site(key, site, replace=True)
    return site


def load_all() -> dict:
    return (yaml.safe_load(SITES_FILE.read_text()) or {}).get("sites", {}) if SITES_FILE.exists() else {}


def save_site(key: str, site: dict, replace: bool = False) -> None:
    sites = load_all()
    sites[key] = dict(site) if replace else {**sites.get(key, {}), **site}
    header = ("# Website setups recorded with `cls setup-isp` (or the app's ISP setup tab).\n"
              "# Each ISP: where its address check starts and which elements to use. Safe to edit by hand:\n"
              "# lists are tried in order, and the built-in defaults are tried after them.\n")
    SITES_FILE.write_text(header + yaml.safe_dump({"sites": sites}, sort_keys=False, allow_unicode=True))
    from .scrapers.isps import register_sites
    register_sites()


def save_test(key: str, results: list[dict]) -> None:
    sites = load_all()
    if key in sites:
        sites[key]["last_test"] = {"date": date.today().isoformat(), "results": results}
        save_site(key, sites[key])


def test_site(cfg: dict, key: str, addresses: list[dict], log=print) -> list[dict]:
    """Run the recorded setup automatically on a few addresses; store the outcome in isp_sites.yaml."""
    from .scrapers import Address
    from .scrapers.runner import scrape_one_isp

    out = []
    for a in addresses:
        addr = Address(a["address_primary"], a["city"], a["state"], str(a["zip"]), location_id=str(a["location_id"]))
        r = scrape_one_isp(cfg, addr, key, headless=cfg["scraper"]["headless"], log=lambda *_: None)
        out.append({"address": addr.one_line(), "status": r.status, "plans": len(r.upis),
                    "message": (r.message or "")[:200]})
        log(f"  {addr.one_line()}: {r.status}, {len(r.upis)} plan(s) {r.message or ''}"[:200])
    save_test(key, out)
    return out
