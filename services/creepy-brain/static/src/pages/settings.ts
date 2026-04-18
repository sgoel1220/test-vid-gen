// Settings page — placeholder

export function mount(container: HTMLElement): void {
  container.innerHTML = `
    <div class="section">
      <h2>Settings</h2>
      <p class="muted">Coming soon.</p>
    </div>
  `;
}

export function unmount(): void {
  // nothing to clean up
}
