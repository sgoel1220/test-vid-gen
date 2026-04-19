// Entry point — hash-based router, nav, page lifecycle

import * as workflows from "./pages/workflows.js";
import * as workflowDetail from "./pages/workflow-detail.js";
import * as gpuPods from "./pages/gpu-pods.js";
import * as settings from "./pages/settings.js";

type Page = { mount: (el: HTMLElement, ...args: string[]) => void; unmount: () => void };

let currentPage: Page | null = null;

const routes: { pattern: RegExp; page: Page; extractArgs: (m: RegExpMatchArray) => string[] }[] = [
  { pattern: /^#\/workflows$/, page: workflows, extractArgs: () => [] },
  { pattern: /^#\/workflow\/(.+)$/, page: workflowDetail, extractArgs: (m) => [m[1]] },
  { pattern: /^#\/gpu-pods$/, page: gpuPods, extractArgs: () => [] },
  { pattern: /^#\/settings$/, page: settings, extractArgs: () => [] },
];

function navigate(): void {
  const hash = location.hash || "#/workflows";
  const container = document.getElementById("app")!;

  // Unmount previous page
  if (currentPage) {
    currentPage.unmount();
    currentPage = null;
  }

  // Match route
  for (const route of routes) {
    const m = hash.match(route.pattern);
    if (m) {
      currentPage = route.page;
      route.page.mount(container, ...route.extractArgs(m));
      updateNav(hash);
      return;
    }
  }

  // Default fallback
  location.hash = "#/workflows";
}

function updateNav(hash: string): void {
  document.querySelectorAll("nav a").forEach((a) => {
    const href = a.getAttribute("href") ?? "";
    // Match exact, prefix/, or treat workflow detail as part of workflows
    const active = hash === href
      || hash.startsWith(href + "/")
      || (href === "#/workflows" && hash.startsWith("#/workflow/"));
    a.classList.toggle("active", active);
  });
}

// Init
window.addEventListener("hashchange", navigate);
document.addEventListener("DOMContentLoaded", navigate);
