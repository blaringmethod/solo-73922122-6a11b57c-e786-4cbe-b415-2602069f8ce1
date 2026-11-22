document.addEventListener("click", (event) => {
  const button = event.target.closest("button.sample");
  if (!button) return;
  const field = document.getElementById(button.dataset.target);
  if (!field) return;
  field.value = button.dataset.sample || "";
  field.focus();
});
