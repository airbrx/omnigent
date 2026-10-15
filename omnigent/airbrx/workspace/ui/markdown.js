// Airbrx workspace kernel: the safe markdown subset agents write in chat.
//
// Bold, italics, inline code, bullet and numbered lists, pipe tables (a
// header row, a separator row, then body rows; alignment is ignored),
// blockquotes (`> ` lines, two levels deep), horizontal rules (`---`, `***`,
// `___`) and line breaks; headings render as bold paragraphs. No links, no images, no HTML. Every
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
  // Three or more of one of - * _, optionally spaced, and nothing else.
  const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const QUOTE = /^ {0,3}>[ \t]?(.*)$/;
  // Deeper `>` marks than this stay as the characters they are.
  const QUOTE_DEPTH = 2;

  // One separator cell, already trimmed. The row is split and trimmed by
  // cells(), never matched as a whole: a single pattern with adjacent
  // whitespace runs is quadratic on a long line of spaces.
  const SEPARATOR_CELL = /^:?-+:?$/;

  /** A table row's cells: outer pipes dropped, `\|` is a literal pipe. */
  function cells(line) {
    let rest = line.trim();
    if (rest.startsWith("|")) rest = rest.slice(1);
    if (rest.endsWith("|") && !rest.endsWith("\\|")) rest = rest.slice(0, -1);
    const out = [];
    let cell = "";
    for (let i = 0; i < rest.length; i += 1) {
      if (rest[i] === "\\" && rest[i + 1] === "|") {
        cell += "|";
        i += 1;
      } else if (rest[i] === "|") {
        out.push(cell.trim());
        cell = "";
      } else cell += rest[i];
    }
    out.push(cell.trim());
    return out;
  }

  /** A header line and a separator line with as many columns start a table. */
  function tableStart(line, next) {
    if (!line.includes("|") || next === undefined || !next.includes("|")) return 0;
    const separator = cells(next);
    if (!separator.every((cell) => SEPARATOR_CELL.test(cell))) return 0;
    const width = cells(line).length;
    return width > 0 && separator.length === width ? width : 0;
  }

  /** The table whose header is lines[start], and the index after its last row. */
  function table(lines, start, width) {
    const row = (tag, line) => {
      const values = cells(line).slice(0, width);
      while (values.length < width) values.push("");
      return el("tr", {}, values.map((value) => el(tag, {}, inline(value))));
    };
    const body = el("tbody");
    let end = start + 2;
    for (; end < lines.length; end += 1) {
      const line = lines[end];
      if (!line.trim() || !line.includes("|")) break;
      body.append(row("td", line));
    }
    return {
      node: el("table", {}, el("thead", {}, row("th", lines[start])), body),
      end,
    };
  }

  /** Text as paragraphs, lists, tables and inline marks in a DocumentFragment. */
  function markdown(text) {
    return blocks(String(text ?? "").replace(/\r\n?/g, "\n").split("\n"), 0);
  }

  function blocks(lines, depth) {
    const fragment = document.createDocumentFragment();
    let paragraph = null;
    let list = null;
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (depth < QUOTE_DEPTH && QUOTE.test(line)) {
        paragraph = null;
        list = null;
        const quoted = [];
        for (; index < lines.length; index += 1) {
          const match = QUOTE.exec(lines[index]);
          if (!match) break;
          quoted.push(match[1]);
        }
        index -= 1;
        fragment.append(el("blockquote", {}, blocks(quoted, depth + 1)));
        continue;
      }
      if (RULE.test(line)) {
        paragraph = null;
        list = null;
        fragment.append(el("hr"));
        continue;
      }
      const width = tableStart(line, lines[index + 1]);
      if (width) {
        paragraph = null;
        list = null;
        const { node, end } = table(lines, index, width);
        fragment.append(node);
        index = end - 1;
        continue;
      }
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
