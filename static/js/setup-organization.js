/* Keep choices inside the selected SKPD; the server remains authoritative. */
(() => {
  'use strict';
  const form = document.getElementById('organization-scope-form');
  if (!form) return;
  const skpd = form.elements.skpd_id;
  const scope = form.elements.scope_type;
  const subunit = form.elements.subunit_id;
  function refresh(reset) {
    for (const option of scope.options) {
      option.disabled = option.hidden = option.dataset.skpdId !== skpd.value;
    }
    if (reset || scope.selectedOptions[0]?.disabled) {
      const first = Array.from(scope.options).find(option => !option.disabled);
      if (first) first.selected = true;
    }
    for (const option of subunit.options) {
      option.disabled = option.hidden = Boolean(option.value) && option.dataset.skpdId !== skpd.value;
    }
    if (reset || scope.value !== 'subunit' || subunit.selectedOptions[0]?.disabled) subunit.value = '';
    subunit.disabled = scope.value !== 'subunit';
    subunit.required = !subunit.disabled;
  }
  skpd.addEventListener('change', () => refresh(true));
  scope.addEventListener('change', () => refresh(false));
  refresh(false);
})();
