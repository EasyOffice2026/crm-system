export async function api<T>(
  path: string,
  method = "GET",
  body?: object | FormData,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers:
      body && !(body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : undefined,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      window.dispatchEvent(new Event("crm-session-expired"));
    }
    const result: { detail?: unknown } = await response
      .json()
      .catch(() => ({}));
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : response.status === 422
          ? "Please check the form fields and amounts."
          : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
export function today() {
  return new Date().toLocaleDateString("en-CA");
}
export function futureDate(days: number) {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return value.toLocaleDateString("en-CA");
}
export function text(form: FormData, name: string) {
  return String(form.get(name) ?? "");
}
export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
