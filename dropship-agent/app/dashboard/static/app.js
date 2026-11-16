// Show a readable message when a dashboard button fails (instead of silently doing nothing).
document.addEventListener("htmx:responseError", function (e) {
  var msg = "Something went wrong.";
  try { msg = JSON.parse(e.detail.xhr.responseText).detail || msg; } catch (_) {}
  alert(msg);
});
document.addEventListener("htmx:sendError", function () {
  alert("Couldn't reach the dashboard. Is `python run.py dashboard` still running?");
});
