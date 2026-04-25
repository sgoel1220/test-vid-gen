// Minimal DOM patching — only updates children that actually changed.
// Preserves scroll position, expand/collapse state, audio playback, and focus.

export function patchHTML(target: HTMLElement, newHTML: string): void {
  const template = document.createElement("template");
  template.innerHTML = newHTML;
  const newNodes = template.content;

  // Fast path: if target is empty, just set innerHTML
  if (!target.firstChild) {
    target.innerHTML = newHTML;
    return;
  }

  reconcileChildren(target, newNodes);
}

function reconcileChildren(parent: HTMLElement, newContent: DocumentFragment): void {
  const oldChildren = Array.from(parent.childNodes);
  const newChildren = Array.from(newContent.childNodes);

  const max = Math.max(oldChildren.length, newChildren.length);
  for (let i = 0; i < max; i++) {
    const oldChild = oldChildren[i];
    const newChild = newChildren[i];

    if (!oldChild && newChild) {
      // New node added
      parent.appendChild(newChild.cloneNode(true));
      continue;
    }

    if (oldChild && !newChild) {
      // Old node removed
      parent.removeChild(oldChild);
      continue;
    }

    if (!oldChild || !newChild) continue;

    // Different node types — replace
    if (oldChild.nodeType !== newChild.nodeType) {
      parent.replaceChild(newChild.cloneNode(true), oldChild);
      continue;
    }

    // Text nodes — update if different
    if (oldChild.nodeType === Node.TEXT_NODE) {
      if (oldChild.textContent !== newChild.textContent) {
        oldChild.textContent = newChild.textContent;
      }
      continue;
    }

    // Element nodes
    if (oldChild.nodeType === Node.ELEMENT_NODE) {
      const oldEl = oldChild as HTMLElement;
      const newEl = newChild as HTMLElement;

      // Different tag — replace entirely
      if (oldEl.tagName !== newEl.tagName) {
        parent.replaceChild(newEl.cloneNode(true), oldEl);
        continue;
      }

      // Skip elements currently playing audio
      if (oldEl.tagName === "AUDIO" && !(oldEl as HTMLAudioElement).paused) {
        continue;
      }

      // Skip focused elements (textareas, inputs) to avoid losing user input
      if (oldEl === document.activeElement) {
        continue;
      }

      // For .section divs, compare innerHTML and skip if identical
      if (oldEl.classList.contains("section")) {
        if (oldEl.innerHTML === newEl.innerHTML) {
          continue; // no change in this section
        }
      }

      // Update attributes
      patchAttributes(oldEl, newEl);

      // Recurse into children
      const frag = document.createDocumentFragment();
      while (newEl.firstChild) frag.appendChild(newEl.firstChild);
      reconcileChildren(oldEl, frag);
    }
  }

  // Remove excess old children (iterate backwards to avoid index shifts)
  while (parent.childNodes.length > newChildren.length) {
    parent.removeChild(parent.lastChild!);
  }
}

function patchAttributes(oldEl: HTMLElement, newEl: HTMLElement): void {
  // Remove old attributes not in new
  for (const attr of Array.from(oldEl.attributes)) {
    if (!newEl.hasAttribute(attr.name)) {
      oldEl.removeAttribute(attr.name);
    }
  }
  // Set new/changed attributes
  for (const attr of Array.from(newEl.attributes)) {
    if (oldEl.getAttribute(attr.name) !== attr.value) {
      oldEl.setAttribute(attr.name, attr.value);
    }
  }
}
