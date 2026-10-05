/* Small DOM helpers shared by the page modules.
 *
 * Nothing here knows about prices.  SVG nodes are made by letting the HTML parser
 * read a one-element fragment, which gives them the right namespace without this
 * file having to spell out a namespace address.
 */

export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  const { text, class: className, translate, ...rest } = options;
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (translate === false) node.setAttribute("translate", "no");
  for (const [key, value] of Object.entries(rest)) {
    if (value === undefined || value === null || value === false) continue;
    node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

function fragment(markup) {
  const template = document.createElement("template");
  template.innerHTML = markup;
  return template.content;
}

/** An SVG element with attributes and children. */
export function svg(tag, attributes = {}, children = []) {
  const node = fragment(`<svg><${tag}></${tag}></svg>`).firstChild.firstChild;
  for (const [key, value] of Object.entries(attributes)) {
    if (value !== undefined && value !== null && value !== false) {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

/** A linear icon from the page's inline sprite; decorative, so hidden from readers. */
export function icon(name, className = "") {
  const markup = `<svg class="icon ${className}" aria-hidden="true" focusable="false">`
    + `<use href="/sprite.svg#i-${name}"></use></svg>`;
  return fragment(markup).firstChild;
}
