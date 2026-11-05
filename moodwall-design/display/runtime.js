/* 무드월 미니 런타임: 디자인 시안 템플릿({{값}}, sc-for, sc-if)을 일반 브라우저에서 그려줍니다. */
(function () {
  function get(scope, path) {
    var parts = path.trim().split("."), v = scope;
    for (var i = 0; i < parts.length; i++) { if (v == null) return undefined; v = v[parts[i]]; }
    return v;
  }
  function hole(str, scope) {
    var m = str.match(/^\s*\{\{\s*([^}]+?)\s*\}\}\s*$/);
    if (m) return get(scope, m[1]);
    return str.replace(/\{\{\s*([^}]+?)\s*\}\}/g, function (_, p) { var v = get(scope, p); return v == null ? "" : String(v); });
  }
  function build(node, scope, out, comp) {
    if (node.nodeType === 3) { out.push(document.createTextNode(hole(node.nodeValue, scope))); return; }
    if (node.nodeType !== 1) return;
    var tag = node.tagName.toLowerCase();
    if (tag === "sc-for") {
      var list = hole(node.getAttribute("list") || "", scope) || [], as = node.getAttribute("as") || "item";
      list.forEach(function (it, idx) { var sc = Object.create(scope); sc[as] = it; sc.index = idx; kids(node, sc, out, comp); });
      return;
    }
    if (tag === "sc-if") { if (hole(node.getAttribute("value") || "", scope)) kids(node, scope, out, comp); return; }
    var el = node.namespaceURI === "http://www.w3.org/2000/svg" ? document.createElementNS(node.namespaceURI, node.tagName) : document.createElement(tag);
    for (var i = 0; i < node.attributes.length; i++) {
      var a = node.attributes[i], n = a.name, v = a.value;
      if (/^hint-/.test(n)) continue;
      if (/^on/i.test(n)) { var fn = get(comp, v.replace(/[{}\s]/g, "")); if (typeof fn === "function") el.addEventListener(n.slice(2).toLowerCase(), fn.bind(comp)); continue; }
      var val = hole(v, scope);
      if (val === false || val == null) continue;
      if (val === true) val = "";
      el.setAttribute(n, String(val));
    }
    var sub = []; kids(node, scope, sub, comp); sub.forEach(function (c) { el.appendChild(c); });
    out.push(el);
  }
  function kids(node, scope, out, comp) { for (var c = node.firstChild; c; c = c.nextSibling) build(c, scope, out, comp); }
  function morph(cur, next) {
    var a = cur.childNodes, b = next.childNodes, i;
    for (i = 0; i < b.length; i++) {
      var x = a[i], y = b[i];
      if (!x) { cur.appendChild(y.cloneNode(true)); continue; }
      if (x.nodeType !== y.nodeType || x.nodeName !== y.nodeName) { cur.replaceChild(y.cloneNode(true), x); continue; }
      if (x.nodeType === 3) { if (x.nodeValue !== y.nodeValue) x.nodeValue = y.nodeValue; continue; }
      if (x.nodeType === 1) {
        for (var k = 0; k < y.attributes.length; k++) { var at = y.attributes[k]; if (x.getAttribute(at.name) !== at.value) x.setAttribute(at.name, at.value); }
        for (k = x.attributes.length - 1; k >= 0; k--) { if (!y.hasAttribute(x.attributes[k].name)) x.removeAttribute(x.attributes[k].name); }
        morph(x, y);
      }
    }
    while (a.length > b.length) cur.removeChild(cur.lastChild);
  }
  window.DCLogic = function () {};
  window.DCLogic.prototype.setState = function (o) { this.state = Object.assign({}, this.state || {}, o); if (this.__render) this.__render(); };
  window.MoodWall = {
    mount: function (Component, defaults, host, w, h) {
      var tpl = document.getElementById("mw-template").content;
      var q = new URLSearchParams(location.search), props = {};
      Object.keys(defaults).forEach(function (k) { props[k] = q.has(k) ? q.get(k) : defaults[k]; });
      var comp = new Component(); comp.props = props; comp.state = comp.state || {};
      var root = document.createElement("div"); host.appendChild(root);
      var first = true;
      comp.__render = function () {
        var out = [], frag = document.createElement("div");
        kids(tpl, comp.renderVals(), out, comp); out.forEach(function (n) { frag.appendChild(n); });
        if (first) { while (frag.firstChild) root.appendChild(frag.firstChild); first = false; } else morph(root, frag);
      };
      comp.__render();
      if (comp.componentDidMount) comp.componentDidMount();
      function fit() { var s = Math.min(innerWidth / w, innerHeight / h); host.style.transform = "scale(" + s + ")"; host.style.left = (innerWidth - w * s) / 2 + "px"; host.style.top = (innerHeight - h * s) / 2 + "px"; }
      addEventListener("resize", fit); fit();
      window.mwSetProp = function (k, v) { var prev = Object.assign({}, comp.props); comp.props[k] = v; comp.__render(); if (comp.componentDidUpdate) comp.componentDidUpdate(prev); };
      return comp;
    }
  };
})();
