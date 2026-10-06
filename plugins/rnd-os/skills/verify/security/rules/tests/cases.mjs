// Fixture for security/rules/bos-node.yaml. "ruleid: X" = the next line must be reported by rule X;
// "ok: X" = rule X must stay quiet on the next line. tests/test_verify.py runs it when the real opengrep is installed.
import { readFile } from "node:fs/promises";
import { join } from "node:path";

export async function secretLogged() {
  // ruleid: bos-secret-in-response-or-log
  console.log("cfg", process.env.SHEET_SECRET);
  // ok: bos-secret-in-response-or-log
  console.log("started");
}

export async function ssrf(req) {
  const body = await req.json();
  // ruleid: bos-request-to-fetch-url
  await fetch(body.callback, { method: "POST" });
  // ok: bos-request-to-fetch-url
  await fetch("https://script.google.com/macros/s/x/exec", { method: "POST" });
}

export async function traversal(req) {
  const name = new URL(req.url).searchParams.get("f");
  // ruleid: bos-request-to-filesystem
  return readFile(join("content", name), "utf8");
}

export function code(x) {
  // ruleid: bos-dynamic-code
  return new Function("return " + x)();
}

export async function sendsEverything(env = process.env, doFetch = fetch) {
  const candidates = [];
  for (const n of Object.keys(env)) candidates.push(env[n]);
  for (const secret of candidates) {
    // ruleid: bos-credential-sent, bos-env-values-sent
    await doFetch("https://api.netlify.com/api/v1/sites/x", { headers: { Authorization: `Bearer ${secret}` } });
  }
}

export async function sendsOne(token) {
  // ruleid: bos-credential-sent
  // ok: bos-env-values-sent
  await fetch("https://api.netlify.com/api/v1/sites/x", { headers: { Authorization: `Bearer ${token}` } });
}
