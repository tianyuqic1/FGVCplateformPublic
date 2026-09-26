let csrfToken = "";

export function getCsrfToken() { return csrfToken; }
export function setCsrfToken(value) { csrfToken = value || ""; }
