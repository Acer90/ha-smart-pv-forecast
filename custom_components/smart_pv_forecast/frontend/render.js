const rendered = new WeakMap();

function patchNode(live, before, after) {
  // Compare render templates, not live focus, expanded details or tooltip state.
  if (before.isEqualNode(after)) return;
  if (before.nodeType !== after.nodeType || before.nodeName !== after.nodeName) {
    live.replaceWith(after.cloneNode(true));
    return;
  }
  if (after.nodeType === Node.TEXT_NODE) {
    live.nodeValue = after.nodeValue;
    return;
  }
  if (after.nodeType === Node.ELEMENT_NODE) {
    for (const attribute of before.attributes) {
      if (!after.hasAttribute(attribute.name)) live.removeAttribute(attribute.name);
    }
    for (const attribute of after.attributes) {
      if (before.getAttribute(attribute.name) !== attribute.value) {
        live.setAttribute(attribute.name, attribute.value);
      }
    }
  }
  const oldNodes = [...before.childNodes], newNodes = [...after.childNodes];
  const liveNodes = [...live.childNodes];
  for (let index = 0; index < Math.max(oldNodes.length, newNodes.length); index++) {
    if (!newNodes[index]) liveNodes[index]?.remove();
    else if (!oldNodes[index]) live.append(newNodes[index].cloneNode(true));
    else patchNode(liveNodes[index], oldNodes[index], newNodes[index]);
  }
}

export function updateRenderedContent(root, html) {
  const previous = rendered.get(root);
  if (previous?.html === html) return false;
  const template = document.createElement("template");
  template.innerHTML = html;
  if (!previous) root.replaceChildren(template.content.cloneNode(true));
  else patchNode(root, previous.content, template.content);
  rendered.set(root, { html, content: template.content });
  return true;
}

export function resetRenderedContent(root) {
  rendered.delete(root);
}
