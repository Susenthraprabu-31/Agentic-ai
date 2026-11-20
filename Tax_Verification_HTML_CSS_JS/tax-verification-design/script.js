'use strict';
// Visual interaction only: no network requests, tokens, cookies, or website access.
const widget = document.getElementById('verification-widget');
const action = document.getElementById('verification-action');
const label = document.getElementById('action-label');
const status = document.getElementById('demo-status');
const info = document.getElementById('info-dialog');
let pending;
action.addEventListener('click', () => {
  if (widget.dataset.state === 'verified') {
    widget.dataset.state = 'idle';
    label.textContent = 'Verify you are human';
    status.textContent = '';
    action.setAttribute('aria-label', 'Run local verification animation demo');
    return;
  }
  widget.dataset.state = 'checking';
  action.disabled = true;
  label.textContent = 'Checking…';
  status.textContent = 'Local visual demo only.';
  pending = window.setTimeout(() => {
    widget.dataset.state = 'verified';
    label.textContent = 'Demo verified';
    status.textContent = 'Animation complete. No real verification was performed. Click again to reset.';
    action.disabled = false;
    action.setAttribute('aria-label', 'Reset local verification animation demo');
  }, 1400);
});
document.querySelectorAll('[data-info]').forEach(button => {
  button.addEventListener('click', () => info.showModal());
});
window.addEventListener('pagehide', () => window.clearTimeout(pending));
