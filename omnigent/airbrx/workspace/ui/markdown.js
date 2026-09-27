// Airbrx workspace kernel: the safe markdown subset agents write in chat.
//
// Bold, italics, inline code, bullet and numbered lists, and line breaks;
// headings render as bold paragraphs. No links, no images, no HTML. Every
// piece is built as DOM nodes with text content, so any markup in the text
// (which can quote customer data) shows as the characters it is and never
// becomes an element. Anything outside the subset shows as written.
(() => {
  "use strict";
  const AW = (window.AirbrxWorkspace = window.AirbrxWorkspace || {});
  const el = (...args) => AW.el(...args);

  // The inline-marks pattern, verbatim from the first agent workspace.
  const INLINE = new RegExp(
    [
      /`([^`\n]+)`/.source,
      /\*\*(?=\S)([^\n]*?\S)\*\*/.source,
      /(?<!\w)__(?=\S)([^\n]*?\S)__(?!\w)/.source,
      /\*([^\s*](?:[^*\n]*?[^\s*])?)\*/.source,
      /(?<!\w)_([^\s_](?:[^_\n]*?[^\s_])?)_(?!\w)/.source,
    ].join("|"),
  );

  /** One line's inline marks, as nodes. */
  function inline(text) {
    const out = [];
    let rest = String(text);
    for (let match = INLINE.exec(rest); match; match = INLINE.exec(rest)) {
      if (match.index) out.push(document.createTextNode(rest.slice(0, match.index)));
      const [, code, bold, bold2, em, em2] = match;
      if (code !== undefined) out.push(el("code", {}, code));
      else if (bold !== undefined || bold2 !== undefined)
        out.push(el("strong", {}, inline(bold ?? bold2)));
      else out.push(el("em", {}, inline(em ?? em2)));
      rest = rest.slice(match.index + match[0].length);
    }
    if (rest) out.push(document.createTextNode(rest));
    return out;
  }

  const BULLET = /^\s*[-*+]\s+(.*)$/;
  const NUMBERED = /^\s*(\d{1,9})[.)]\s+(.*)$/;
  const HEADING = /^\s*#{1,6}\s+(.*)$/;

  /** Text as paragraphs, lists and inline marks in a DocumentFragment. */
  function markdown(text) {
    const fragment = document.createDocumentFragment();
    let paragraph = null;
    let list = null;
    for (const line of String(text ?? "").replace(/\r\n?/g, "\n").split("\n")) {
      const bullet = BULLET.exec(line);
      const numbered = !bullet && NUMBERED.exec(line);
      if (bullet || numbered) {
        paragraph = null;
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = el(
            tag,
            numbered && numbered[1] !== "1"
              ? { start: Number(numbered[1]) }
              : {},
          );
          fragment.append(list);
        }
        list.append(el("li", {}, inline(bullet ? bullet[1] : numbered[2])));
        continue;
      }
      list = null;
      if (!line.trim()) {
        paragraph = null;
        continue;
      }
      const heading = HEADING.exec(line);
      if (heading) {
        paragraph = null;
        fragment.append(el("p", {}, el("strong", {}, inline(heading[1]))));
        continue;
      }
      if (paragraph) paragraph.append(el("br"), ...inline(line));
      else fragment.append((paragraph = el("p", {}, inline(line))));
    }
    return fragment;
  }

  AW.markdown = markdown;
  AW.inline = inline;
})();
