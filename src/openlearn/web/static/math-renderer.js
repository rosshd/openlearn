"use strict";

// Local, committed-output-only presentation. Never typeset source previews or streams.
window.OpenLearnMath = (() => {
  const namespace = "http://www.w3.org/1998/Math/MathML";
  const commands = new Set([
    "frac", "dfrac", "tfrac", "sqrt", "cdot", "times", "div", "pm", "mp",
    "le", "leq", "ge", "geq", "ne", "neq", "approx", "equiv", "in", "notin",
    "alpha", "beta", "gamma", "delta", "theta", "lambda", "mu", "pi", "sigma",
    "sum", "prod", "int", "infty", "partial", "nabla", "det", "log", "ln",
    "sin", "cos", "tan", "exp", "min", "max", "lim", "text", "mathrm",
    "begin", "end", "left", "right", "\\", "{", "}", "_", "%", "#", "&",
    " ", ",", ";", ":", "!",
  ]);
  const environments = new Set(["matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix"]);
  const elements = new Set([
    "math", "semantics", "annotation", "mrow", "mi", "mn", "mo", "mtext",
    "mfrac", "msqrt", "mroot", "msub", "msup", "msubsup", "munder", "mover",
    "munderover", "mtable", "mtr", "mtd", "mstyle", "mspace", "mpadded",
  ]);
  const attributes = new Set([
    "xmlns", "display", "encoding", "mathvariant", "displaystyle", "scriptlevel",
    "fence", "stretchy", "form", "separator", "lspace", "rspace", "minsize",
    "maxsize", "width", "height", "depth", "columnalign", "rowalign",
    "columnspacing", "rowspacing", "accent", "accentunder",
  ]);

  function supported(tex) {
    if (typeof tex !== "string" || tex.length > 2000) return false;
    let depth = 0;
    for (const char of tex) {
      if (char === "{" && ++depth > 16) return false;
      if (char === "}") depth -= 1;
    }
    for (const match of tex.matchAll(/\\([A-Za-z]+|[^A-Za-z])/g)) {
      if (!commands.has(match[1])) return false;
    }
    for (const match of tex.matchAll(/\\(?:begin|end)\{([^}]*)\}/g)) {
      if (!environments.has(match[1])) return false;
    }
    return true;
  }

  function safeTree(math) {
    const nodes = [math, ...math.querySelectorAll("*")];
    if (nodes.length > 512) return false;
    return nodes.every((node) => node.namespaceURI === namespace && elements.has(node.localName)
      && [...node.attributes].every((attribute) => attributes.has(attribute.name)
        && (attribute.name !== "mathvariant" || attribute.value === "normal")));
  }

  function render(target, tex, display = false) {
    target.classList.add("math-expression", "math-fallback");
    if (display) {
      target.classList.add("math-display");
      target.tabIndex = 0; // Allows keyboard scrolling of a wide display equation.
    }
    const fallback = document.createElement("code");
    fallback.textContent = tex;
    target.replaceChildren(fallback);
    if (!window.katex || typeof window.MathMLElement === "undefined" || !supported(tex)) return;
    try {
      const temporary = document.createElement("span");
      window.katex.render(tex, temporary, {
        output: "mathml", displayMode: display, trust: false, strict: "error",
        throwOnError: true, maxSize: 10, maxExpand: 100, macros: {},
      });
      const math = temporary.querySelector("math");
      if (!math || !safeTree(math)) return;
      target.replaceChildren(math);
      target.classList.remove("math-fallback");
    } catch {
      // Retain escaped source. Never insert vendor error messages as HTML.
    }
  }

  for (const target of document.querySelectorAll("[data-math-expression]")) {
    render(target, target.querySelector("code")?.textContent || "", target.dataset.mathDisplay === "true");
  }
  return Object.freeze({render});
})();
