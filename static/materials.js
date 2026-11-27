// Submit the complete filter form so search, type and ownership stay combined.
const materialsFilters = document.getElementById('materials-filters');

if (materialsFilters) {
    materialsFilters.querySelectorAll('select').forEach((dropdown) => {
        dropdown.addEventListener('change', () => {
            materialsFilters.requestSubmit();
        });
    });
}
