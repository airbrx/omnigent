// Airbrx workspace kernel: DOM helpers, copied from the first agent workspace and made
// generic. Every kernel file attaches to window.AirbrxWorkspace and is loaded
// by a plain <script> before the agent's app.js; there is no bundler.
//
// Data is never parsed as markup. `el` builds nodes and text nodes only.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});

  // An attribute that navigates or loads. A `javascript:` value there would run
  // code, so it is dropped rather than set.
  const URL_ATTRS = new Set(["href", "src", "action", "formaction", "xlink:href"]);
  const SCRIPT_URL = /^[\s\u0000-\u001f]*(javascript|vbscript|data):/i;

  /**
   * A new element. `attrs` values of undefined, null or false are skipped;
   * true sets an empty attribute; `class` sets className; an `on<event>` key
   * adds its function as a listener (anything that is not a function is
   * ignored, so no string ever becomes an inline handler). Children are nodes
   * or text; arrays are flattened, and null, undefined and false are skipped.
   */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) {
        if (typeof value === "function")
          node.addEventListener(key.slice(2), value);
      } else if (URL_ATTRS.has(key.toLowerCase()) && SCRIPT_URL.test(String(value)))
        continue;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
    }
    return node;
  }

  AW.el = el;
  AW.$ = (id) => document.getElementById(id);
})();
