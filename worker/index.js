// Auto Electrix – serves the website and emails form enquiries.
//
// Settings (Cloudflare dashboard → the Worker → Settings → Variables and Secrets):
//   TO_ADDRESS   the email address enquiries are sent to (must be a verified
//                destination in Email Routing)
//   FROM_ADDRESS an address on the website's domain, e.g. website@yourdomain.uk
import { EmailMessage } from "cloudflare:email";

const MAX_ATTACHMENT = 8 * 1024 * 1024; // matches the limit shown on the forms
const SKIP_FIELDS = new Set(["form-name", "form-title", "bot-field"]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/submit" && request.method === "POST") {
      return handleForm(request, env);
    }
    return env.ASSETS.fetch(request);
  },
};

async function handleForm(request, env) {
  const thanks = new URL("/thank-you/", request.url).toString();
  let data;
  try {
    data = await request.formData();
  } catch {
    return errorPage(400);
  }

  // Bots fill in the hidden "bot-field". Pretend it worked and send nothing.
  if (data.get("bot-field")) return Response.redirect(thanks, 303);

  const title = clean(data.get("form-title")) || "Website enquiry";
  const name = clean(data.get("Full Name"));
  const lines = [];
  const files = [];
  for (const [key, value] of data) {
    if (SKIP_FIELDS.has(key)) continue;
    if (typeof value === "string") {
      if (value.trim()) lines.push(`${key.replace(/\[\]$/, "")}: ${value.trim()}`);
    } else if (value.size > 0 && value.size <= MAX_ATTACHMENT) {
      files.push(value);
    }
  }

  const email = clean(data.get("Email"));
  const replyTo = /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(email) ? email : "";
  const subject = `New ${title}${name ? ` from ${name}` : ""}`;
  const text = `${title}\n${"-".repeat(title.length)}\n\n${lines.join("\n")}\n`;

  try {
    const raw = await buildEmail({ from: env.FROM_ADDRESS, to: env.TO_ADDRESS, replyTo, subject, text, files });
    await env.ENQUIRIES.send(new EmailMessage(env.FROM_ADDRESS, env.TO_ADDRESS, raw));
  } catch (err) {
    console.error("Enquiry email failed:", err);
    return errorPage(500);
  }
  return Response.redirect(thanks, 303);
}

// Removes line breaks so form values can't add extra email headers.
function clean(value) {
  return typeof value === "string" ? value.replace(/[\r\n]+/g, " ").trim() : "";
}

function encodeHeader(value) {
  return /^[\x20-\x7e]*$/.test(value) ? value : `=?UTF-8?B?${toBase64(new TextEncoder().encode(value))}?=`;
}

function toBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary).replace(/.{76}/g, "$&\r\n");
}

async function buildEmail({ from, to, replyTo, subject, text, files }) {
  const boundary = `ae-${crypto.randomUUID()}`;
  const domain = from.split("@")[1];
  const headers = [
    `From: Auto Electrix Website <${from}>`,
    `To: ${to}`,
    replyTo ? `Reply-To: ${replyTo}` : "",
    `Subject: ${encodeHeader(subject)}`,
    `Date: ${new Date().toUTCString()}`,
    `Message-ID: <${crypto.randomUUID()}@${domain}>`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/mixed; boundary="${boundary}"`,
  ].filter(Boolean);

  const parts = [
    [
      `--${boundary}`,
      'Content-Type: text/plain; charset="UTF-8"',
      "Content-Transfer-Encoding: base64",
      "",
      toBase64(new TextEncoder().encode(text)),
    ].join("\r\n"),
  ];
  for (const file of files) {
    const filename = file.name.replace(/["\r\n\\]/g, "_") || "upload";
    parts.push(
      [
        `--${boundary}`,
        `Content-Type: ${file.type || "application/octet-stream"}; name="${filename}"`,
        "Content-Transfer-Encoding: base64",
        `Content-Disposition: attachment; filename="${filename}"`,
        "",
        toBase64(new Uint8Array(await file.arrayBuffer())),
      ].join("\r\n"),
    );
  }
  return `${headers.join("\r\n")}\r\n\r\n${parts.join("\r\n")}\r\n--${boundary}--\r\n`;
}

function errorPage(status) {
  const body = `<!DOCTYPE html><html lang="en-GB"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sorry – Auto Electrix</title>
<link rel="stylesheet" href="/assets/css/style.css"></head><body>
<div class="container section"><h1>Sorry, your enquiry didn't send</h1>
<p>Something went wrong on our side. Please call us on <a href="tel:01202509430">01202 509430</a>, or go back and try again.</p>
<a class="btn" href="javascript:history.back()">Go Back »</a></div></body></html>`;
  return new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
