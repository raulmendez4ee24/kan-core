#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require("fs");
const path = require("path");
const axios = require("axios");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");
const dotenv = require("dotenv");

const ROOT_DIR = path.resolve(__dirname, "..");
dotenv.config({ path: path.join(ROOT_DIR, ".env") });

const KAN_BASE_URL = (process.env.KAN_BASE_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const KAN_CLIENT_ID = String(process.env.KAN_CLIENT_ID || "").trim();
const KAN_CLIENT_TOKEN = String(process.env.KAN_CLIENT_TOKEN || "").trim();
const WA_ALLOWED_NUMBERS = String(process.env.WA_ALLOWED_NUMBERS || "")
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);
const WA_PROCESS_FROM_ME = String(process.env.WA_PROCESS_FROM_ME || "false").toLowerCase() === "true";
const WA_FROM_ME_PREFIX = String(process.env.WA_FROM_ME_PREFIX || "/ai").trim();
const WA_DATA_PATH = process.env.WA_DATA_PATH || path.join(ROOT_DIR, ".run", "wa_qr_runtime");
const WA_HEADLESS = String(process.env.WA_HEADLESS || "true").toLowerCase() !== "false";
const WA_CHROME_EXECUTABLE_PATH = String(process.env.WA_CHROME_EXECUTABLE_PATH || "").trim();
const WA_CLIENT_ID = String(process.env.WA_CLIENT_ID || "kan-whatsapp-qr-v2").trim();
const WA_PAIR_PHONE = String(process.env.WA_PAIR_PHONE || "").replace(/\D/g, "");
const WA_RETRY_MS = Number(process.env.WA_RETRY_MS || 5000);

let activeClient = null;
let restartInProgress = false;
let keepAliveStarted = false;
const recentlySentReplies = new Set();

function assertEnv() {
  if (!KAN_CLIENT_ID || !KAN_CLIENT_TOKEN) {
    throw new Error("KAN_CLIENT_ID / KAN_CLIENT_TOKEN missing in .env");
  }
}

function normalizeNumber(chatId) {
  return String(chatId || "").split("@")[0];
}

function allowedNumber(chatId) {
  if (!WA_ALLOWED_NUMBERS.length) return true;
  const num = normalizeNumber(chatId);
  return WA_ALLOWED_NUMBERS.includes(num);
}

async function callKan(message, from) {
  const payload = {
    session_id: `wa:${from}`,
    message,
    channel: "whatsapp",
    metadata: {
      source: "wa_qr_bridge",
      wa_from: from,
    },
  };
  const res = await axios.post(`${KAN_BASE_URL}/chat/message`, payload, {
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": KAN_CLIENT_ID,
      "X-Client-Token": KAN_CLIENT_TOKEN,
    },
    timeout: 30000,
  });
  const body = res.data || {};
  const content = String(body.content || body.message || body.text || "").trim();
  return content || "Listo.";
}

function errorDetail(err) {
  if (!err) return "unknown error";
  const parts = [];
  if (err.name) parts.push(err.name);
  if (err.message) parts.push(err.message);
  if (err.code) parts.push(`code=${err.code}`);
  if (Array.isArray(err.errors) && err.errors.length) {
    const inner = err.errors
      .slice(0, 4)
      .map((e) => `${e && e.code ? e.code + ":" : ""}${e && e.message ? e.message : String(e)}`)
      .join(" | ");
    parts.push(`inner=[${inner}]`);
  }
  return parts.join(" | ");
}

function isRecoverableBrowserError(err) {
  const detail = String((err && (err.stack || err.message)) || err || "").toLowerCase();
  return (
    detail.includes("target closed") ||
    detail.includes("protocol error") ||
    detail.includes("execution context was destroyed") ||
    detail.includes("session closed")
  );
}

function scheduleRestart(reason) {
  if (restartInProgress) return;
  restartInProgress = true;
  console.log(`[wa-qr] reiniciando bridge en ${WA_RETRY_MS}ms. motivo: ${reason}`);
  const doRestart = () => {
    setTimeout(async () => {
      restartInProgress = false;
      try {
        await start();
      } catch (err) {
        console.error(`[wa-qr] restart failed: ${err.message || err}`);
        scheduleRestart("restart_failed");
      }
    }, WA_RETRY_MS);
  };
  if (activeClient) {
    Promise.resolve(activeClient.destroy())
      .catch(() => {})
      .finally(() => {
        activeClient = null;
        doRestart();
      });
    return;
  }
  doRestart();
}

async function start() {
  assertEnv();
  fs.mkdirSync(WA_DATA_PATH, { recursive: true });

  const client = new Client({
    authStrategy: new LocalAuth({
      dataPath: WA_DATA_PATH,
      clientId: WA_CLIENT_ID,
    }),
    ...(WA_PAIR_PHONE
      ? {
          pairWithPhoneNumber: {
            phoneNumber: WA_PAIR_PHONE,
            showNotification: true,
            intervalMs: 180000,
          },
        }
      : {}),
    puppeteer: {
      headless: WA_HEADLESS,
      args: ["--no-sandbox", "--disable-setuid-sandbox"],
      ...(WA_CHROME_EXECUTABLE_PATH ? { executablePath: WA_CHROME_EXECUTABLE_PATH } : {}),
    },
  });
  activeClient = client;

  client.on("qr", (qr) => {
    console.log("\n=== ESCANEA ESTE QR EN WHATSAPP ===\n");
    qrcode.generate(qr, { small: true });
    console.log("\nQR listo. Esperando autenticación...\n");
  });

  client.on("authenticated", () => {
    console.log("[wa-qr] authenticated");
  });

  client.on("code", (code) => {
    console.log(`\n[wa-qr] CODIGO DE VINCULACION: ${code}\n`);
    console.log("[wa-qr] En tu celular: Dispositivos vinculados -> Vincular con numero de telefono.");
  });

  client.on("ready", () => {
    console.log("[wa-qr] ready");
  });

  client.on("disconnected", (reason) => {
    console.log(`[wa-qr] disconnected: ${reason}`);
    scheduleRestart(`disconnected:${reason}`);
  });

  client.on("message", async (msg) => {
    try {
      if (!msg || !msg.from) return;
      if (recentlySentReplies.has(msg.id?._serialized)) {
        recentlySentReplies.delete(msg.id?._serialized);
        return;
      }
      if (!WA_PROCESS_FROM_ME && msg.fromMe) return;
      if (!allowedNumber(msg.from)) return;
      let text = String(msg.body || "").trim();
      if (!text) return;
      if (msg.fromMe) {
        // Prevent self-reply loops: only process explicit self commands.
        if (!WA_FROM_ME_PREFIX || !text.toLowerCase().startsWith(WA_FROM_ME_PREFIX.toLowerCase())) {
          return;
        }
        text = text.slice(WA_FROM_ME_PREFIX.length).trim();
        if (!text) return;
      }
      const reply = await callKan(text, msg.from);
      const sent = await msg.reply(reply);
      if (sent?.id?._serialized) {
        recentlySentReplies.add(sent.id._serialized);
        setTimeout(() => recentlySentReplies.delete(sent.id._serialized), 120000);
      }
    } catch (err) {
      const detail = errorDetail(err);
      console.error(`[wa-qr] message handling error: ${detail}`);
      if (String(detail).toLowerCase().includes("econnrefused")) {
        console.error(`[wa-qr] backend unreachable: ${KAN_BASE_URL}/chat/message`);
      }
      if (msg?.fromMe) return;
      try {
        await msg.reply("Error procesando mensaje. Reintenta en unos segundos.");
      } catch (_ignored) {}
    }
  });

  try {
    await client.initialize();
    // Keep process alive for long-running bridge mode under nohup/system scripts.
    if (!keepAliveStarted) {
      setInterval(() => {}, 60_000);
      keepAliveStarted = true;
    }
  } catch (err) {
    if (isRecoverableBrowserError(err)) {
      console.error(`[wa-qr] recoverable browser error: ${err.message || err}`);
      scheduleRestart("recoverable_browser_error");
      return;
    }
    throw err;
  }
}

process.on("uncaughtException", (err) => {
  if (isRecoverableBrowserError(err)) {
    console.error(`[wa-qr] uncaught recoverable error: ${err.message || err}`);
    scheduleRestart("uncaught_exception");
    return;
  }
  console.error(`[wa-qr] fatal uncaughtException: ${err.message || err}`);
  process.exit(1);
});

process.on("unhandledRejection", (reason) => {
  if (isRecoverableBrowserError(reason)) {
    const msg = reason && reason.message ? reason.message : String(reason);
    console.error(`[wa-qr] unhandled recoverable rejection: ${msg}`);
    scheduleRestart("unhandled_rejection");
    return;
  }
  console.error(`[wa-qr] fatal unhandledRejection: ${String(reason)}`);
  process.exit(1);
});

process.on("exit", (code) => {
  console.log(`[wa-qr] process exit code=${code}`);
});

start().catch((err) => {
  console.error(`[wa-qr] fatal: ${err.message || err}`);
  process.exit(1);
});
